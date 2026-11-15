#!/bin/bash
# socboard-deploy (issue #237, Deploy item D20). The whole content of the Systems Manager document
# `socboard-deploy` (deploy_document.tf); it runs as root on the server. One input: an image digest.
# It takes that image to a running app behind host Caddy, or leaves the current release serving and
# exits non-zero. Decision record: _docs/decisions/*-deploy-script.md.
#
# Order: lock, digest check, "already deployed" check, pull by digest and label check, the release's
# compose.yaml and compose/db_init.py, the secret, the stack's variable file, db, db-init, migrate
# and seed, a candidate app on a spare loopback port, the swap, the checks after it, clean-up.
# Nothing before the swap touches the running app. The swap is not zero downtime.
#
# Never printed: this script prints only the fixed lines of the failure table and, after exits 6, 7
# and 9, the last 20 log lines of the app with the secret's eight values replaced. It never runs
# with tracing on, never prints a command's own output, and passes no secret on a command line.
# No migration is undone by a restore. The script runs no SQL and sets no database URL.
#
# Test-only settings (tests/test_deploy_script.py sets them so the script runs without root; the
# defaults below are the real ones): SOCBOARD_DEPLOY_RUN_DIR, SOCBOARD_DEPLOY_OPT_DIR,
# SOCBOARD_DEPLOY_WAIT (seconds) and SOCBOARD_DEPLOY_POLL (seconds).
#
# The document replaces the one parameter reference on the next line before the script runs; run any
# other way, the script takes the digest as its first argument. Keep that the only such reference.
DIGEST_PARAM='{{ digest }}'

set -u
export LC_ALL=C
export DEBIAN_FRONTEND=noninteractive
umask 077

IMAGE_REPO='ghcr.io/andrew-chung-au/socboard'
SOURCE_LABEL='https://github.com/andrew-chung-au/socboard'
RAW_BASE='https://raw.githubusercontent.com/andrew-chung-au/socboard'
SECRET_ID='socboard/app'
SECRET_REGION='us-east-1'
IMDS='http://169.254.169.254'
DATA_DIR='/srv/socboard-data'
# The stack network's gateway as the app sees Caddy's requests (README, "The forwarded address").
# VERIFY: Compose pins this gateway; the check below fails the run if the network has another.
PROXY_ADDR='10.231.77.1'
STACK_NETWORK='socboard_stack'
# VERIFY: the name Compose gives the app container (project `socboard`, service `app`, number 1).
APP='socboard-app-1'
CAND='socboard-deploy-candidate'
CAND_PORT='127.0.0.1:8001:8000'
RUN_DIR=${SOCBOARD_DEPLOY_RUN_DIR:-/run/socboard-deploy}
OPT_DIR=${SOCBOARD_DEPLOY_OPT_DIR:-/opt/socboard}
WAIT=${SOCBOARD_DEPLOY_WAIT:-90}
POLL=${SOCBOARD_DEPLOY_POLL:-3}
VARS="$RUN_DIR/stack-vars"
NAMES=(SOCBOARD_TOKEN_SECRET SOCBOARD_INGEST_API_KEY SOCBOARD_SEED_PASSWORD_ANALYST_A
  SOCBOARD_SEED_PASSWORD_ANALYST_B SOCBOARD_SEED_PASSWORD_LEAD STACK_PG_SUPERUSER_PASSWORD
  STACK_PG_OWNER_PASSWORD STACK_PG_APP_PASSWORD)

CAND_UP=0
REDACT=()
declare -A S

say() { printf '%s\n' "$1"; }

# A Go template for `docker --format`, built so the script holds no doubled brace: the document
# would read one as a parameter reference.
tpl() { printf '%s%s%s%s' '{' '{' "$1" '}}'; }

text_for() {
  case "$1" in
    2) say 'socboard-deploy: refused: the digest must be sha256: followed by 64 lower-case hex digits. Nothing was changed.' ;;
    3) say 'socboard-deploy: the application secret could not be read or is not usable. The current release was not changed.' ;;
    4) say "socboard-deploy: the image or the Compose files could not be fetched, or are not this repository's. The current release was not changed." ;;
    5) say 'socboard-deploy: db-init, migrate or seed failed. The current release was not changed.' ;;
    6) say 'socboard-deploy: the new app container did not become healthy. The current release was not changed.' ;;
    7) say "socboard-deploy: the new release failed after the swap; $2." ;;
    8) say 'socboard-deploy: another run is in progress. Nothing was changed.' ;;
    9) say 'socboard-deploy: the new release failed after the swap and the previous release could not be restored.' ;;
    10) say "socboard-deploy: the stack network's gateway is not the configured proxy address. The current release was not changed." ;;
  esac
}

die() {
  text_for "$1" "${2:-}"
  exit "$1"
}

# The last 20 lines of a container's log, each secret value replaced, longest value first.
# The only `docker logs` form in this script.
logs_of() {
  local out line v
  out=$(docker logs --tail 20 "$1" 2>&1) || return 0
  [ -n "$out" ] || return 0
  while IFS= read -r line; do
    for v in "${REDACT[@]}"; do line=${line//"$v"/[redacted]}; done
    printf '%s\n' "$line"
  done <<<"$out"
}

# Exits 6, 7 and 9: the fixed line, then the log lines (read before anything removes the container).
die_logs() {
  local code=$1 container=$2 lines
  lines=$(logs_of "$container")
  text_for "$code" "${3:-}"
  [ -z "$lines" ] || printf '%s\n' "$lines"
  exit "$code"
}

cleanup() {
  local rc=$?
  [ "$CAND_UP" = 1 ] && docker rm -f "$CAND" >/dev/null 2>&1
  rm -f "$VARS"
  exec 9>&-
  exit "$rc"
}
# shortcut: a signal ends the run with no fixed line (it cannot say more), clean-up still runs; the next run starts from what Docker shows.
trap cleanup EXIT
trap 'exit 143' HUP INT TERM

compose() {
  docker compose --project-directory "$REL" --env-file "$VARS" -f "$REL/compose.yaml" -f "$REL/deploy.override.yaml" "$@"
}

# healthy within $WAIT seconds, counted from the call; a container that is not running fails at once.
wait_healthy() {
  local c=$1 start=$SECONDS run st
  while :; do
    run=$(docker inspect --format "$(tpl .State.Running)" "$c" 2>/dev/null) || run=false
    [ "$run" = true ] || return 1
    st=$(docker inspect --format "$(tpl .State.Health.Status)" "$c" 2>/dev/null) || st=''
    [ "$st" = healthy ] && return 0
    [ $((SECONDS - start)) -ge "$WAIT" ] && return 1
    sleep "$POLL"
  done
}

# The hostname main.tf derives: the public address with dots as dashes, then .sslip.io.
public_hostname() {
  local token ip
  token=$(curl --silent --fail --max-time 5 --proto '=http' -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' "$IMDS/latest/api/token") || return 1
  # The session token goes in on standard input: it is a credential, so never on a command line.
  ip=$(printf 'header = "X-aws-ec2-metadata-token: %s"\n' "$token" | curl --silent --fail --max-time 5 --proto '=http' --config - "$IMDS/latest/meta-data/public-ipv4") || return 1
  [[ $ip =~ ^[0-9]+(\.[0-9]+){3}$ ]] || return 1
  printf '%s.sslip.io' "${ip//./-}"
}

# The checks after the swap; each must pass.
check_release() {
  local host start code
  wait_healthy "$APP" || return 1
  # Passed by name only: the values travel in the environment of docker, not in its arguments.
  SOCBOARD_SEED_PASSWORD_LEAD=${S[SOCBOARD_SEED_PASSWORD_LEAD]} SOCBOARD_INGEST_API_KEY=${S[SOCBOARD_INGEST_API_KEY]} \
    docker run --rm --pull never --network host -e SOCBOARD_SEED_PASSWORD_LEAD -e SOCBOARD_INGEST_API_KEY \
    "$IMAGE" python -m socboard.smoke http://127.0.0.1:8000 >/dev/null 2>&1 || return 1
  systemctl is-active --quiet caddy || return 1
  host=$(public_hostname) || return 1
  start=$SECONDS
  while :; do
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 --proto '=https' \
      --resolve "$host:443:127.0.0.1" "https://$host/api/health") || code=0
    [ "$code" = 200 ] && return 0
    [ $((SECONDS - start)) -ge "$WAIT" ] && return 1
    sleep "$POLL"
  done
}

# --- 1. the lock -------------------------------------------------------------------------------
# The lock file is the one file an "already deployed" run may create (it lives in a memory file system).
mkdir -p "$RUN_DIR" || die 8
exec 9>"$RUN_DIR/lock" || die 8
flock -n 9 || die 8

# --- 2. the digest check (the document's allowedPattern is the first check) ----------------------
digest=${1-$DIGEST_PARAM}
hex=${digest#sha256:}
if [ "${#digest}" -ne 71 ] || [ "$hex" = "$digest" ] || [[ $hex == *[!0-9a-f]* ]]; then
  die 2
fi
IMAGE="$IMAGE_REPO@$digest"

# --- 3. already deployed -----------------------------------------------------------------------
PREV=$(docker inspect --format "$(tpl .Config.Image)" "$APP" 2>/dev/null) || PREV=''
if [ "$PREV" = "$IMAGE" ]; then
  st=$(docker inspect --format "$(tpl .State.Health.Status)" "$APP" 2>/dev/null) || st=''
  if [ "$st" = healthy ]; then
    say 'socboard-deploy: already deployed. Nothing was changed.'
    exit 0
  fi
fi
# Only a digest reference of this repository counts as a previous release to restore.
if [ "${#PREV}" -ne $((${#IMAGE_REPO} + 72)) ] || [[ $PREV != "$IMAGE_REPO"@sha256:* ]] || [[ ${PREV##*:} == *[!0-9a-f]* ]]; then
  PREV=''
fi

# A candidate left by a killed run.
docker rm -f "$CAND" >/dev/null 2>&1

# --- 4. the image and its labels ---------------------------------------------------------------
docker pull "$IMAGE" >/dev/null 2>&1 || die 4
source_label=$(docker image inspect --format "$(tpl 'index .Config.Labels "org.opencontainers.image.source"')" "$IMAGE" 2>/dev/null) || die 4
revision=$(docker image inspect --format "$(tpl 'index .Config.Labels "org.opencontainers.image.revision"')" "$IMAGE" 2>/dev/null) || die 4
[ "$source_label" = "$SOURCE_LABEL" ] || die 4
if [ "${#revision}" -ne 40 ] || [[ $revision == *[!0-9a-f]* ]]; then die 4; fi

# --- 5. the Compose files of the image's revision, as data -----------------------------------------
REL="$OPT_DIR/releases/$revision-${hex:0:12}"
mkdir -p "$REL/compose" || die 4
for f in compose.yaml compose/db_init.py; do
  if ! curl --silent --fail --max-time 60 --proto '=https' --tlsv1.2 --output "$REL/$f.part" "$RAW_BASE/$revision/$f" || [ ! -s "$REL/$f.part" ]; then
    rm -f "$REL/$f.part"
    die 4
  fi
  mv -f "$REL/$f.part" "$REL/$f" || die 4
done
# The restart policy and the proxy address are set here, so compose.yaml stays as the repository has it.
{
  printf '%s\n' 'services:' '  db:' '    restart: unless-stopped' '  app:' '    restart: unless-stopped' \
    '    environment:' "      SOCBOARD_TRUSTED_PROXIES: $PROXY_ADDR"
} >"$REL/deploy.override.yaml" || die 4

# --- 6. the secret ---------------------------------------------------------------------------------
if ! python3 -c 'import boto3' >/dev/null 2>&1; then
  apt-get install -y python3-boto3 >/dev/null 2>&1 || die 3
fi
read -r -d '' PYCODE <<'PY'
import json, re, sys
names = sys.argv[1:]
try:
    import boto3
    client = boto3.client("secretsmanager", region_name="us-east-1")
    data = json.loads(client.get_secret_value(SecretId="socboard/app")["SecretString"])
except Exception:
    sys.exit(1)
if not isinstance(data, dict) or set(data) != set(names):
    sys.exit(1)
for name in names:
    value = data[name]
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=@%:,-]+", value):
        sys.exit(1)
    if name.startswith("STACK_PG_") and not re.fullmatch(r"[A-Za-z0-9]+", value):
        sys.exit(1)
for name in names:
    print(name + "=" + data[name])
PY
lines=$(python3 -c "$PYCODE" "${NAMES[@]}" 2>/dev/null </dev/null) || die 3
while IFS= read -r line; do
  S[${line%%=*}]=${line#*=}
done <<<"$lines"
unset lines
[ "${#S[@]}" -eq "${#NAMES[@]}" ] || die 3
for n in "${NAMES[@]}"; do
  [ -n "${S[$n]:-}" ] || die 3
  v=${S[$n]}
  i=0
  while [ "$i" -lt "${#REDACT[@]}" ] && [ "${#REDACT[$i]}" -ge "${#v}" ]; do i=$((i + 1)); done
  REDACT=("${REDACT[@]:0:$i}" "$v" "${REDACT[@]:$i}")
done

# --- 7. the variable file (a memory file system, mode 0600, removed at exit) -----------------------------
{
  for n in "${NAMES[@]}"; do printf '%s=%s\n' "$n" "${S[$n]}"; done
  printf '%s=%s\n' STACK_IMAGE "$IMAGE" STACK_PGDATA_DIR "$DATA_DIR"
} >"$VARS" || die 3
chmod 600 "$VARS" || die 3

# --- 8. the database, the gateway check, then db-init, migrate and seed ---------------------------------
compose up -d --no-deps --wait --wait-timeout 120 --pull never --no-build db >/dev/null 2>&1 || die 5
gateway=$(docker network inspect --format "$(tpl 'range .IPAM.Config')$(tpl .Gateway) $(tpl end)" "$STACK_NETWORK" 2>/dev/null) || die 10
gateway=${gateway%% }
[ "$gateway" = "$PROXY_ADDR" ] || die 10
for service in db-init migrate seed; do
  compose run --rm --no-deps --pull never "$service" >/dev/null 2>&1 || die 5
done

# --- 9. the candidate ---------------------------------------------------------------------------------
CAND_UP=1
compose run --no-deps -d --pull never --name "$CAND" -p "$CAND_PORT" app >/dev/null 2>&1 || die_logs 6 "$CAND"
wait_healthy "$CAND" || die_logs 6 "$CAND"
docker rm -f "$CAND" >/dev/null 2>&1
CAND_UP=0

# --- 10. the swap and the checks; a failure restores the previous release ---------------------------
swapped_ok=0
if compose up -d --no-deps --pull never --no-build app >/dev/null 2>&1 && check_release; then
  swapped_ok=1
fi
if [ "$swapped_ok" = 1 ]; then
  ln -sfn "$REL" "$OPT_DIR/current.new" && mv -T "$OPT_DIR/current.new" "$OPT_DIR/current"
  say 'socboard-deploy: deployed.'
  exit 0
fi

lines=$(logs_of "$APP")
if [ -z "$PREV" ]; then
  docker stop "$APP" >/dev/null 2>&1
  text_for 7 'there was no previous release to restore'
  [ -z "$lines" ] || printf '%s\n' "$lines"
  exit 7
fi
PREV_DIR=$(readlink "$OPT_DIR/current" 2>/dev/null) || PREV_DIR=''
[ -f "$PREV_DIR/compose.yaml" ] && [ -f "$PREV_DIR/deploy.override.yaml" ] || PREV_DIR=$REL
REL=$PREV_DIR
if STACK_IMAGE=$PREV compose up -d --no-deps --pull never --no-build app >/dev/null 2>&1 && wait_healthy "$APP"; then
  text_for 7 'the previous release was restored'
  [ -z "$lines" ] || printf '%s\n' "$lines"
  exit 7
fi
text_for 9
[ -z "$lines" ] || printf '%s\n' "$lines"
exit 9
