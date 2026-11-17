#!/bin/bash
# socboard-deploy (issue #237, Deploy item D20). The whole content of the Systems Manager document
# `socboard-deploy` (deploy_document.tf); it runs as root on the server. Two inputs: an image digest and
# an environment, `dev` (the default) or `prod` (issue #267). It takes that image to a running app behind
# host Caddy, or leaves the current release serving and exits non-zero. A prod run uses prod's values
# from one fixed table below and touches nothing of dev's. Decision records: _docs/decisions/*-deploy-script.md
# and _docs/decisions/*-deploy-script-environment.md.
#
# Order: lock, digest check, environment check, "already deployed" check, pull by digest and label check, the release's
# compose.yaml and compose/db_init.py, the database image pull, the secret, the stack's variable file, db, db-init, migrate
# and seed, a candidate app on a spare loopback port, the swap, the checks after it, clean-up.
# Nothing before the swap touches the running app. The swap is not zero downtime.
#
# Never printed: this script prints only the fixed lines of the failure table and, after exits 6, 7
# and 9, the last 20 log lines of the app, and after exit 5 the failing step's name and the last 20 lines of
# its output, all with the secret's eight values replaced. It never runs with tracing on, prints a command's
# own output only in those cases, and passes no secret on a command line.
# No migration is undone by a restore. The script runs no SQL and sets no database URL.
#
# Nothing of its behaviour is read from the environment except what Systems Manager and the host
# provide: the folders and waits below are constants (tests/test_deploy_script.py runs a copy with
# those four lines replaced), and the names written into the variable file are unset at the start.
# Decision record: _docs/decisions/*-deploy-script-ambient-inputs.md.
#
# The first thing the script does is start itself again once under an empty environment (the block
# below), so a variable of the starting shell (a start-up file name, shell options, a field splitter, a
# program search path) never reaches the run. The new run gets a fixed search path and a marker, nothing
# else. It needs no region variable (the secret read names its region) and no home folder.
# The marker is honoured only when the environment is exactly that clean one; any other variable, an
# exported function, or a search path that is not the fixed one starts the script again. One limit: a
# start-up file named by such a variable in the shell that starts this script is read by that shell before
# the first line, which no script can prevent; the new run never reads it. The same holds for an exported
# function that replaces a command this block uses.
# The digest travels as the first argument of the new run.
CLEAN_PATH='/usr/sbin:/usr/bin:/sbin:/bin'
set +x
[ -n "${BASH_VERSION-}" ] && [ "${CLEAN_START-}" = 1 ] && [ "$PATH" = "$CLEAN_PATH" ] || exec /usr/bin/env -i PATH="$CLEAN_PATH" CLEAN_START=1 /bin/bash --norc --noprofile "$0" "$@" || exit 1
# Everything above this line is POSIX sh: Systems Manager starts the script with `sh` (dash on Ubuntu), which
# reads one line at a time and takes the hand-over above, so it never parses a line below. From here on only
# the bash run of the hand-over (or an outer bash that passed the test above) is reading, and it checks strictly.
clean=1
while IFS= read -r name; do
  case $name in CLEAN_START | PATH | PWD | SHLVL | _) ;; *) clean=0 ;; esac
done < <(compgen -e)
[ -z "$(compgen -A function)" ] || clean=0
[ "$clean" = 1 ] || exec /usr/bin/env -i PATH="$CLEAN_PATH" CLEAN_START=1 /bin/bash --norc --noprofile "$0" "$@" || exit 1
unset clean name
case $- in *x*) exit 1 ;; esac

# The document replaces the two parameter references on the next lines before the script runs; run any
# other way, the script takes the digest as its first argument and the environment as its second.
# Keep these the only such references, each written once: Systems Manager replaces every occurrence, so a
# third (in a comparison, say) would be replaced too. The unreplaced environment reference is recognised
# by building its text (tpl, below), never by writing it again.
DIGEST_PARAM='{{ digest }}'
ENV_PARAM='{{ environment }}'

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
DATA_DIR='/srv/socboard-data/postgres'
# The stack network's gateway as the app sees Caddy's requests (README, "The forwarded address").
# VERIFY: Compose pins this gateway; the check below fails the run if the network has another.
PROXY_ADDR='10.231.77.1'
# The Compose project name (`name:` in compose.yaml), the app's loopback ports (APP_PORT is the app's, CAND_HOST_PORT the
# candidate's) and the app's port inside the container: one place each.
PROJECT='socboard'
APP_PORT='8000'
CAND_HOST_PORT='8001'
CONTAINER_PORT='8000'
# The environment's values table (issue #267). The lines above and the three below are dev's row, the values of a run
# with `dev` or no environment; the `prod` branch of the case after the lock replaces each one. Nothing here or there
# is read from the environment, the document or the arguments but the checked name. $OPT_DIR below stays one constant:
# ENV_DIR is the environment's folder under it.
HOST_PREFIX=''
STACK_VARS=()
RUN_DIR='/run/socboard-deploy'
OPT_DIR='/opt/socboard'
ENV_DIR="$OPT_DIR"
WAIT=90
POLL=3
VARS="$RUN_DIR/stack-vars"
NAMES=(SOCBOARD_TOKEN_SECRET SOCBOARD_INGEST_API_KEY SOCBOARD_SEED_PASSWORD_ANALYST_A
  SOCBOARD_SEED_PASSWORD_ANALYST_B SOCBOARD_SEED_PASSWORD_LEAD STACK_PG_SUPERUSER_PASSWORD
  STACK_PG_OWNER_PASSWORD STACK_PG_APP_PASSWORD)
# Compose lets a shell variable override the same name in the variable file, so none of the names
# the script writes there (the ten of a dev run, and prod's three more) may be inherited.
unset "${NAMES[@]}" STACK_IMAGE STACK_PGDATA_DIR STACK_PROJECT STACK_NET STACK_APP_PORT

CAND_UP=0
LOCKED=0
REDACT=()
declare -A S

say() { printf '%s\n' "$1"; }

# A Go template for `docker --format`, built so the script holds no doubled brace: the document
# would read one as a parameter reference.
tpl() { printf '%s%s%s%s' '{' '{' "$1" '}}'; }

text_for() {
  case "$1" in
    2)
      case "${2:-}" in
        environment) say 'socboard-deploy: refused: the environment must be dev or prod. Nothing was changed.' ;;
        *) say 'socboard-deploy: refused: the digest must be sha256: followed by 64 lower-case hex digits. Nothing was changed.' ;;
      esac
      ;;
    3) say 'socboard-deploy: the application secret could not be read or is not usable. The current release was not changed.' ;;
    4) say "socboard-deploy: the image, the database image or the Compose files could not be fetched, or are not this repository's. The current release was not changed." ;;
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
  local out
  out=$(docker logs --tail 20 "$1" 2>&1) || return 0
  redact_tail "$out"
}

# The last 20 lines of the text in $1, each secret value replaced, longest value first.
redact_tail() {
  local -a all
  local line v start
  [ -n "$1" ] || return 0
  mapfile -t all <<<"$1"
  start=0
  [ "${#all[@]}" -gt 20 ] && start=$((${#all[@]} - 20))
  for line in "${all[@]:start}"; do
    for v in "${REDACT[@]}"; do line=${line//"$v"/[redacted]}; done
    printf '%s\n' "$line"
  done
}

# Exit 5: the fixed line, the failing step's name, then the last 20 lines of its output (redacted). $2 is the step's
# captured output; it is never printed on success.
die_step() {
  local lines
  lines=$(redact_tail "$2")
  text_for 5
  # Not a fixed line of the failure table (it varies with the step), so it is printed with printf, which the CI reader does not count.
  printf 'socboard-deploy: failed step: %s\n' "$1"
  [ -z "$lines" ] || printf '%s\n' "$lines"
  exit 5
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
  # A run that lost the lock removes nothing: the variable file and the candidate belong to the run that holds it.
  if [ "$LOCKED" = 1 ]; then
    [ "$CAND_UP" = 1 ] && docker rm -f "$CAND" >/dev/null 2>&1
    rm -f "$VARS"
  fi
  exec 9>&-
  exit "$rc"
}
# shortcut: a signal ends the run with no fixed line (it cannot say more), clean-up still runs; the next run starts from what Docker shows.
trap cleanup EXIT
trap 'exit 143' HUP INT TERM

compose() {
  docker compose --project-directory "$REL" --env-file "$VARS" -f "$REL/compose.yaml" -f "$REL/deploy.override.yaml" "$@"
}

# Every variable compose.yaml requires (`${NAME:?...}`) or interpolates without a default. Compose reads the whole file
# before any command, `pull` included; the secret is not read yet, so the pull sees a fixed placeholder, never a value.
# tests/test_deploy_script.py fails when compose.yaml needs a name this list lacks.
PULL_VARS=(SOCBOARD_TOKEN_SECRET SOCBOARD_INGEST_API_KEY SOCBOARD_SEED_PASSWORD_ANALYST_A
  SOCBOARD_SEED_PASSWORD_ANALYST_B SOCBOARD_SEED_PASSWORD_LEAD STACK_PG_SUPERUSER_PASSWORD
  STACK_PG_OWNER_PASSWORD STACK_PG_APP_PASSWORD)

# The one call that runs before the variable file exists: no --env-file content, placeholders in this call's environment only.
compose_pull_db() {
  local n
  local -a placeholders=()
  for n in "${PULL_VARS[@]}"; do placeholders+=("$n=placeholder-not-a-secret"); done
  # A prod run renders the file with prod's project and network, never dev's defaults (these are not secrets).
  [ "${#STACK_VARS[@]}" -eq 0 ] || placeholders+=("${STACK_VARS[@]}")
  env "${placeholders[@]}" docker compose --project-directory "$REL" --env-file /dev/null -f "$REL/compose.yaml" -f "$REL/deploy.override.yaml" pull db
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
  printf '%s%s.sslip.io' "$HOST_PREFIX" "${ip//./-}"
}

# The checks after the swap; each must pass.
check_release() {
  local host start code
  wait_healthy "$APP" || return 1
  # Passed by name only: the values travel in the environment of docker, not in its arguments.
  SOCBOARD_SEED_PASSWORD_LEAD=${S[SOCBOARD_SEED_PASSWORD_LEAD]} SOCBOARD_INGEST_API_KEY=${S[SOCBOARD_INGEST_API_KEY]} \
    docker run --rm --pull never --network host -e SOCBOARD_SEED_PASSWORD_LEAD -e SOCBOARD_INGEST_API_KEY \
    "$IMAGE" python -m socboard.smoke "http://127.0.0.1:$APP_PORT" >/dev/null 2>&1 || return 1
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

# The last step of the swap: `current` names the release that now runs. A failure leaves `current` as it was.
link_current() {
  ln -sfn "$REL" "$ENV_DIR/current.new" && mv -T "$ENV_DIR/current.new" "$ENV_DIR/current" && return 0
  rm -f "$ENV_DIR/current.new"
  return 1
}

# --- 1. the lock -------------------------------------------------------------------------------
# The lock file is the one file an "already deployed" run may create (it lives in a memory file system).
mkdir -p "$RUN_DIR" || die 8
exec 9>"$RUN_DIR/lock" || die 8
flock -n 9 || die 8
LOCKED=1

# --- 2. the digest check (the document's allowedPattern is the first check) ----------------------
digest=${1-$DIGEST_PARAM}
hex=${digest#sha256:}
if [ "${#digest}" -ne 71 ] || [ "$hex" = "$digest" ] || [[ $hex == *[!0-9a-f]* ]]; then
  die 2
fi
IMAGE="$IMAGE_REPO@$digest"

# --- 2b. the environment check and the values table (the document's allowedPattern is the first check) ----------
# The second argument when one is given (a third argument is refused), else the document's value; run by hand with
# one argument, the reference stays as written and means dev. The case matches the whole value, nothing else is run.
if [ "$#" -ge 3 ]; then
  envname=''
elif [ "$#" -eq 2 ]; then
  envname=$2
elif [ "$ENV_PARAM" = "$(tpl ' environment ')" ]; then
  envname=dev
else
  envname=$ENV_PARAM
fi
case $envname in
  dev) ;;
  prod)
    HOST_PREFIX='prod-'
    SECRET_ID='socboard/app-prod'
    DATA_DIR='/srv/socboard-data/postgres-prod'
    PROJECT='socboard-prod'
    APP_PORT='8100'
    CAND_HOST_PORT='8101'
    NET_PREFIX='10.231.78'
    PROXY_ADDR="$NET_PREFIX.1"
    ENV_DIR="$OPT_DIR/prod"
    # Prod only: the variable file and the database image pull's placeholders carry them, dev's carry none (compose.yaml's defaults).
    STACK_VARS=(STACK_PROJECT="$PROJECT" STACK_NET="$NET_PREFIX" STACK_APP_PORT="$APP_PORT")
    ;;
  *) die 2 environment ;;
esac
unset envname
STACK_NETWORK="${PROJECT}_stack"
# VERIFY: the name Compose gives the app container (project, service `app`, number 1).
APP="$PROJECT-app-1"
# VERIFY: the name Compose gives the database container (project, service `db`, number 1).
DB="$PROJECT-db-1"
CAND="$PROJECT-deploy-candidate"
CAND_PORT="127.0.0.1:$CAND_HOST_PORT:$CONTAINER_PORT"

# --- 3. already deployed -----------------------------------------------------------------------
PREV=$(docker inspect --format "$(tpl .Config.Image)" "$APP" 2>/dev/null) || PREV=''
if [ "$PREV" = "$IMAGE" ]; then
  st=$(docker inspect --format "$(tpl .State.Health.Status)" "$APP" 2>/dev/null) || st=''
  run=$(docker inspect --format "$(tpl .State.Running)" "$APP" 2>/dev/null) || run=''
  if [ "$st" = healthy ] && [ "$run" = true ]; then
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
REL="$ENV_DIR/releases/$revision-${hex:0:12}"
mkdir -p "$REL/compose" || die 4
for f in compose.yaml compose/db_init.py; do
  if ! curl --silent --fail --max-time 60 --proto '=https' --tlsv1.2 --output "$REL/$f.part" "$RAW_BASE/$revision/$f" || [ ! -s "$REL/$f.part" ]; then
    rm -f "$REL/$f.part"
    die 4
  fi
  mv -f "$REL/$f.part" "$REL/$f" || die 4
done
# umask 077 made both files root 0600, and db-init (the app image's uid 10001) reads compose/db_init.py through a bind mount,
# which keeps the host mode. Both files are public files of this repository: no secret in either (the variable file stays 0600).
chmod 755 "$REL/compose" && chmod 644 "$REL/compose.yaml" "$REL/compose/db_init.py" || die 4
# The restart policy and the proxy address are set here, so compose.yaml stays as the repository has it.
{
  printf '%s\n' 'services:' '  db:' '    restart: unless-stopped' '  app:' '    restart: unless-stopped' \
    '    environment:' "      SOCBOARD_TRUSTED_PROXIES: $PROXY_ADDR"
} >"$REL/deploy.override.yaml" || die 4
# The database image (pinned by digest in the fetched compose.yaml) is pulled now, so a failure leaves the old app and the secret alone.
compose_pull_db >/dev/null 2>&1 || die 4

# --- 6. the secret ---------------------------------------------------------------------------------
if ! python3 -c 'import boto3' >/dev/null 2>&1; then
  apt-get install -y python3-boto3 >/dev/null 2>&1 || die 3
fi
read -r -d '' PYCODE <<'PY'
import json, re, sys
secret_id, names = sys.argv[1], sys.argv[2:]
try:
    import boto3
    client = boto3.client("secretsmanager", region_name="us-east-1")
    data = json.loads(client.get_secret_value(SecretId=secret_id)["SecretString"])
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
lines=$(python3 -c "$PYCODE" "$SECRET_ID" "${NAMES[@]}" 2>/dev/null </dev/null) || die 3
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
  [ "${#STACK_VARS[@]}" -eq 0 ] || printf '%s\n' "${STACK_VARS[@]}"
} >"$VARS" || die 3
chmod 600 "$VARS" || die 3

# --- 8. the database, the gateway check, then db-init, migrate and seed ---------------------------------
out=$(compose up -d --no-deps --wait --wait-timeout 120 --pull never --no-build db 2>&1) || die_step db "$out
$(logs_of "$DB")"
gateway=$(docker network inspect --format "$(tpl 'range .IPAM.Config')$(tpl .Gateway) $(tpl end)" "$STACK_NETWORK" 2>/dev/null) || die 10
gateway=${gateway%% }
[ "$gateway" = "$PROXY_ADDR" ] || die 10
for service in db-init migrate seed; do
  out=$(compose run --rm --no-deps --pull never "$service" 2>&1) || die_step "$service" "$out"
done
unset out

# --- 9. the candidate ---------------------------------------------------------------------------------
CAND_UP=1
compose run --no-deps -d --pull never --name "$CAND" -p "$CAND_PORT" app >/dev/null 2>&1 || die_logs 6 "$CAND"
wait_healthy "$CAND" || die_logs 6 "$CAND"
docker rm -f "$CAND" >/dev/null 2>&1
CAND_UP=0

# --- 10. the swap and the checks; a failure restores the previous release ---------------------------
swapped_ok=0
if compose up -d --no-deps --pull never --no-build app >/dev/null 2>&1 && check_release && link_current; then
  swapped_ok=1
fi
if [ "$swapped_ok" = 1 ]; then
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
PREV_DIR=$(readlink "$ENV_DIR/current" 2>/dev/null) || PREV_DIR=''
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
