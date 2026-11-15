"""The deploy script (#237, D20), run offline with stub programs.

`terraform/server/socboard-deploy.sh` runs as root on the server. Here it runs as an ordinary user
in a temporary folder: the run directory, the release directory and the lock live under the test's
folder because the script reads them from SOCBOARD_DEPLOY_RUN_DIR and SOCBOARD_DEPLOY_OPT_DIR (set
only by this file; the defaults in the script are the real /run/socboard-deploy and /opt/socboard),
and its waits come from SOCBOARD_DEPLOY_WAIT and SOCBOARD_DEPLOY_POLL.

PATH holds only the stubs (docker, curl, python3 with a fake boto3, flock, systemctl, apt-get, which
record every argument in one call log) and a folder of links to the few real programs the script may
use, so a command the script adds outside that list fails the run. Every secret value is a canary:
each run checks that no canary reaches the output, a recorded argument or a file left on the disk.
Nothing here needs root, Docker, AWS, Terraform or the network.
"""

import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "terraform" / "server" / "socboard-deploy.sh"
MAIN_TF = ROOT / "terraform" / "server" / "main.tf"

DIGEST = "sha256:" + "a" * 64
PREVIOUS = "sha256:" + "b" * 64
REPO = "ghcr.io/andrew-chung-au/socboard"
IMAGE = f"{REPO}@{DIGEST}"
PREVIOUS_IMAGE = f"{REPO}@{PREVIOUS}"
REVISION = "0123456789abcdef0123456789abcdef01234567"
APP = "socboard-app-1"
CAND = "socboard-deploy-candidate"
TOKEN = "imdsTokenCanary0001"

SECRET_VALUES = {
    "SOCBOARD_TOKEN_SECRET": "canaryTokenSecret.0123456789abcdefABCDEF",
    "SOCBOARD_INGEST_API_KEY": "canaryIngestKey+0123456789",
    "SOCBOARD_SEED_PASSWORD_ANALYST_A": "canarySeedA/Password01",
    "SOCBOARD_SEED_PASSWORD_ANALYST_B": "canarySeedB=Password02",
    "SOCBOARD_SEED_PASSWORD_LEAD": "canarySeedLead@Password03",
    "STACK_PG_SUPERUSER_PASSWORD": "canarySuperDb0001",
    "STACK_PG_OWNER_PASSWORD": "canaryOwnerDb0002",
    "STACK_PG_APP_PASSWORD": "canaryAppDb0003",
}
OTHER_CANARIES = [TOKEN, "BODYCANARY", "DENIEDCANARY"]

FIXED = {
    2: "socboard-deploy: refused: the digest must be sha256: followed by 64 lower-case hex digits. Nothing was changed.",
    3: "socboard-deploy: the application secret could not be read or is not usable. The current release was not changed.",
    4: "socboard-deploy: the image or the Compose files could not be fetched, or are not this repository's. The current release was not changed.",
    5: "socboard-deploy: db-init, migrate or seed failed. The current release was not changed.",
    6: "socboard-deploy: the new app container did not become healthy. The current release was not changed.",
    "7r": "socboard-deploy: the new release failed after the swap; the previous release was restored.",
    "7n": "socboard-deploy: the new release failed after the swap; there was no previous release to restore.",
    8: "socboard-deploy: another run is in progress. Nothing was changed.",
    9: "socboard-deploy: the new release failed after the swap and the previous release could not be restored.",
    10: "socboard-deploy: the stack network's gateway is not the configured proxy address. The current release was not changed.",
}
DEPLOYED = "socboard-deploy: deployed."
ALREADY = "socboard-deploy: already deployed. Nothing was changed."


def real(name):
    path = shutil.which(name)
    assert path, f"{name} is needed to build the test's tool folder"
    return path


class Result:
    def __init__(self, box, proc):
        self.box = box
        self.code = proc.returncode
        self.out = proc.stdout + proc.stderr
        self.calls = box.calls.read_text(encoding="utf-8").splitlines() if box.calls.exists() else []

    def kinds(self):
        return [k for k in (kind(line) for line in self.calls) if k]

    def lines(self):
        return self.out.splitlines()


def compose_tail(line):
    """The words after the second -f file of a recorded `docker compose` call."""
    return line.split(" -f ")[-1].split()[1:]


def kind(line):
    """A short name for one recorded call, or None for a note line."""
    if line.startswith(("VARS_MODE", "IMG ", "SMOKE_ENV")):
        return None
    words = line.split()
    head = words[0]
    if head == "flock":
        return "flock"
    if head == "systemctl":
        return "caddy-active" if words[1:2] == ["is-active"] else "systemctl-other"
    if head == "apt-get":
        return "apt"
    if head == "python3":
        return "boto3-check" if words[1:] == ["-c", "import", "boto3"] else "secret-read"
    if head == "curl":
        if "raw.githubusercontent.com" in line:
            return "fetch-db-init" if line.endswith("db_init.py") else "fetch-compose"
        if line.endswith("/latest/api/token"):
            return "imds-token"
        if line.endswith("public-ipv4"):
            return "imds-ip"
        return "https-health"
    assert head == "docker", line
    sub = words[1]
    if sub == "pull":
        return "pull"
    if sub == "rm":
        return "rm-cand" if words[-1] == CAND else "rm-other"
    if sub == "stop":
        return "stop-app" if words[-1] == APP else "stop-other"
    if sub == "logs":
        return "logs"
    if sub == "network":
        return "network"
    if sub == "run":
        return "smoke"
    if sub == "image":
        return "label-source" if "image.source" in line else "label-revision"
    if sub == "inspect":
        for needle, name in ((".Config.Image", "inspect-image"), (".State.Health.Status", "inspect-health"), (".State.Running", "inspect-running")):
            if needle in line:
                return name
        return "inspect-other"
    assert sub == "compose", line
    words = compose_tail(line)
    verb, service = words[0], words[-1]
    if verb == "up":
        return f"up-{service}"
    if verb == "run":
        return "run-candidate" if "--name" in words else f"run-{service}"
    return f"compose-{verb}"


STUB_HEAD = r"""#!@BASH@
S="$STUB_STATE"
nl=$'\n'
a="$*"
printf '%s\n' "@NAME@ ${a//"$nl"/ }" >> "$STUB_CALLS"
val() { local v; if [ -f "$S/$1" ]; then IFS= read -r v < "$S/$1"; else v=$2; fi; printf '%s' "$v"; }
"""

DOCKER = r"""
case "$1" in
  pull) [ -n "${STUB_PULL_FAIL:-}" ] && exit 1; exit 0 ;;
  rm) [ "$3" = socboard-deploy-candidate ] && @RM@ -f "$S/cand_exists"; exit 0 ;;
  stop) [ "$2" = socboard-app-1 ] && printf false > "$S/app_running"; exit 0 ;;
  logs) f="$S/logs-$4"; [ -f "$f" ] || exit 1; @TAIL@ -n "$3" "$f"; exit 0 ;;
  network) printf '%s ' "${STUB_GATEWAY-10.231.77.1}"; [ -n "${STUB_NETWORK_FAIL:-}" ] && exit 1; exit 0 ;;
  image)
    case "$4" in
      *image.source*) printf '%s' "${STUB_SOURCE-https://github.com/andrew-chung-au/socboard}" ;;
      *) printf '%s' "${STUB_REVISION-0123456789abcdef0123456789abcdef01234567}" ;;
    esac
    exit 0 ;;
  inspect)
    fmt=$3; name=$4
    if [ "$name" = socboard-app-1 ]; then
      [ -f "$S/app_image" ] || exit 1
      case "$fmt" in
        *Config.Image*) val app_image ;;
        *Health.Status*) val app_health healthy ;;
        *) val app_running true ;;
      esac
      exit 0
    fi
    [ -f "$S/cand_exists" ] || exit 1
    case "$fmt" in
      *Health.Status*) printf '%s' "${STUB_CAND_HEALTH:-healthy}" ;;
      *) printf '%s' "${STUB_CAND_RUNNING:-true}" ;;
    esac
    exit 0 ;;
  run)
    [ -n "${SOCBOARD_SEED_PASSWORD_LEAD:-}" ] && [ -n "${SOCBOARD_INGEST_API_KEY:-}" ] && echo "SMOKE_ENV ok" >> "$STUB_CALLS"
    exit "${STUB_SMOKE_RC:-0}" ;;
  compose)
    shift; envf=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --project-directory|-f) shift 2 ;;
        --env-file) envf=$2; shift 2 ;;
        *) break ;;
      esac
    done
    sub=$1; shift
    if [ -f "$envf" ]; then echo "VARS_MODE $(@STAT@ -c %a "$envf")" >> "$STUB_CALLS"; [ -f "$S/vars-snapshot" ] || @CP@ "$envf" "$S/vars-snapshot"; else echo "VARS_MODE missing" >> "$STUB_CALLS"; fi
    echo "IMG ${STACK_IMAGE-unset}" >> "$STUB_CALLS"
    for last; do :; done
    case "$sub" in
      up)
        case "$last" in
          db) [ -n "${STUB_DB_UP_FAIL:-}" ] && exit 1 ;;
          app)
            # "all" fails the restore too; any other value fails only the first swap (no STACK_IMAGE in the environment).
            [ -n "${STUB_UP_APP_FAIL:-}" ] && { [ "$STUB_UP_APP_FAIL" = all ] || [ -z "${STACK_IMAGE-}" ]; } && exit 1
            img=${STACK_IMAGE-}
            if [ -z "$img" ]; then while IFS= read -r l; do case "$l" in STACK_IMAGE=*) img=${l#STACK_IMAGE=} ;; esac; done < "$envf"; health=${STUB_NEW_HEALTH:-healthy}; else health=${STUB_RESTORE_HEALTH:-healthy}; fi
            printf '%s' "$img" > "$S/app_image"; printf true > "$S/app_running"; printf '%s' "$health" > "$S/app_health" ;;
        esac
        exit 0 ;;
      run)
        case " $* " in
          *" --name "*) [ -n "${STUB_CAND_RUN_FAIL:-}" ] && exit 1; : > "$S/cand_exists" ;;
          *) [ "${STUB_FAIL_SERVICE:-}" = "$last" ] && exit 1 ;;
        esac
        exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
"""

CURL = r"""
out=""; url=""; prev=""
for a in "$@"; do
  case "$prev" in --output|-o) out=$a ;; esac
  prev=$a; url=$a
done
case "$url" in
  *raw.githubusercontent.com*)
    [ -n "${STUB_FETCH_FAIL:-}" ] && exit 22
    if [ -n "${STUB_FETCH_EMPTY:-}" ]; then : > "$out"; else printf 'services: {}\n' > "$out"; fi
    exit 0 ;;
  */latest/api/token)
    [ -n "${STUB_IMDS_FAIL:-}" ] && exit 22
    printf '%s' "$STUB_TOKEN"; exit 0 ;;
  */public-ipv4)
    cfg=""; while IFS= read -r l; do cfg="$cfg$l"; done
    case "$cfg" in *"$STUB_TOKEN"*) ;; *) exit 22 ;; esac
    printf '%s' "${STUB_IP:-203.0.113.10}"; exit 0 ;;
  https://*/api/health)
    [ -n "$out" ] && printf 'BODYCANARY' > "$out"
    printf '%s' "${STUB_HEALTH_CODE:-200}"; exit 0 ;;
esac
exit 22
"""

SYSTEMCTL = 'if [ "$1" = is-active ]; then exit "${STUB_CADDY_RC:-0}"; fi\nexit 0\n'
APT = 'rc=${STUB_APT_RC:-0}; [ "$rc" = 0 ] && : > "$S/boto3_installed"; exit "$rc"\n'
PYTHON3 = (
    'if [ "$2" = "import boto3" ] && [ -n "${STUB_BOTO3_MISSING:-}" ] && [ ! -f "$S/boto3_installed" ]; then exit 1; fi\n'
    'PYTHONPATH="$STUB_PYPATH" exec @PYTHON@ "$@"\n'
)
FLOCK = 'exec @FLOCK@ "$@"\n'

FAKE_BOTO3 = '''import os


class _Client:
    def get_secret_value(self, SecretId):
        assert SecretId == "socboard/app"
        if os.environ.get("STUB_SECRET_RAISE"):
            raise RuntimeError("AccessDenied " + os.environ["STUB_SECRET_RAISE"])
        with open(os.environ["STUB_SECRET_FILE"], encoding="utf-8") as handle:
            return {"SecretString": handle.read()}


def client(name, region_name=None):
    assert name == "secretsmanager" and region_name == "us-east-1"
    return _Client()
'''


class Box:
    """One test's folder: stubs, state, run and release directories, and the call log."""

    def __init__(self, base: Path):
        self.base = base
        self.bin = base / "bin"
        self.tools = base / "tools"
        self.state = base / "state"
        self.run_dir = base / "run" / "socboard-deploy"
        self.opt = base / "opt" / "socboard"
        self.calls = base / "calls.log"
        self.secret_file = base / "secret.json"
        for folder in (self.bin, self.tools, self.state, self.base / "py" / "boto3", self.base / "run", self.base / "opt"):
            folder.mkdir(parents=True)
        (self.base / "py" / "boto3" / "__init__.py").write_text(FAKE_BOTO3, encoding="utf-8")
        # The stubs call their own helpers by absolute path, so the script's PATH stays small.
        self.paths = {"BASH": real("bash"), "RM": real("rm"), "TAIL": real("tail"), "STAT": real("stat"), "CP": real("cp"), "PYTHON": sys.executable, "FLOCK": real("flock")}
        for name, body in (("docker", DOCKER), ("curl", CURL), ("systemctl", SYSTEMCTL), ("apt-get", APT), ("python3", PYTHON3), ("flock", FLOCK)):
            self.stub(name, body)
        # The only real programs the script may run (everything else is a builtin or a stub).
        for name in ("mkdir", "rm", "mv", "ln", "readlink", "chmod", "sleep"):
            (self.tools / name).symlink_to(real(name))
        self.write_secret(dict(SECRET_VALUES))

    def stub(self, name, body):
        text = (STUB_HEAD + body).replace("@NAME@", name)
        for key, value in self.paths.items():
            text = text.replace(f"@{key}@", value)
        path = self.bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def write_secret(self, data):
        self.secret_file.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def set_app(self, image, health="healthy", running="true"):
        (self.state / "app_image").write_text(image, encoding="utf-8")
        (self.state / "app_health").write_text(health, encoding="utf-8")
        (self.state / "app_running").write_text(running, encoding="utf-8")

    def set_logs(self, container, lines):
        (self.state / f"logs-{container}").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def previous_release(self):
        """The files and `current` link an earlier deploy leaves."""
        folder = self.opt / "releases" / "previous"
        folder.mkdir(parents=True)
        (folder / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (folder / "deploy.override.yaml").write_text("services: {}\n", encoding="utf-8")
        (self.opt / "current").symlink_to(folder)
        return folder

    def run(self, *args, digest=DIGEST, env=None, use_default_arg=True):
        argv = list(args) if args else ([digest] if use_default_arg else [])
        environment = {
            "PATH": f"{self.bin}:{self.tools}",
            "SOCBOARD_DEPLOY_RUN_DIR": str(self.run_dir),
            "SOCBOARD_DEPLOY_OPT_DIR": str(self.opt),
            "SOCBOARD_DEPLOY_WAIT": "1",
            "SOCBOARD_DEPLOY_POLL": "0.05",
            "STUB_STATE": str(self.state),
            "STUB_CALLS": str(self.calls),
            "STUB_TOKEN": TOKEN,
            "STUB_SECRET_FILE": str(self.secret_file),
            "STUB_PYPATH": str(self.base / "py"),
        }
        environment.update(env or {})
        proc = subprocess.run([real("bash"), str(SCRIPT), *argv], env=environment, capture_output=True, text=True, timeout=60)
        return Result(self, proc)

    def files_left(self):
        found = []
        for folder in (self.run_dir, self.opt):
            for path in folder.rglob("*") if folder.exists() else []:
                if path.is_file() or path.is_symlink():
                    found.append(path)
        return found


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def assert_safe(r):
    """No canary in the output, a recorded argument or a file left behind; the variable file is gone, was 0600 and the lock is free."""
    calls = "\n".join(r.calls)
    for canary in [*SECRET_VALUES.values(), *OTHER_CANARIES]:
        assert canary not in r.out, f"{canary} reached the output"
        assert canary not in calls, f"{canary} reached a recorded argument"
        for path in r.box.files_left():
            if path.is_file():
                assert canary not in path.read_text(encoding="utf-8", errors="replace"), f"{canary} left in {path}"
    assert not (r.box.run_dir / "stack-vars").exists()
    modes = {line.split()[1] for line in r.calls if line.startswith("VARS_MODE")}
    assert modes <= {"600"}, modes
    with open(r.box.run_dir / "lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if the script left the lock held


def assert_old_app_untouched(r):
    """No stop, kill, down or `up ... app`, and no `rm` of anything but the candidate."""
    assert not {"stop-app", "stop-other", "rm-other", "up-app"} & set(r.kinds()), r.kinds()
    assert not [k for k in r.kinds() if k.startswith("compose-")], r.kinds()


def assert_only(r, code, text):
    assert r.code == code, r.out
    assert r.out == text + "\n"


def assert_candidate_removed(r):
    """When a candidate was started, a later `docker rm -f` of it removed it."""
    kinds = r.kinds()
    if "run-candidate" in kinds:
        assert "rm-cand" in kinds[kinds.index("run-candidate") + 1 :], kinds


# --- the script's text --------------------------------------------------------------------------

SCRIPT_TEXT = SCRIPT.read_text(encoding="utf-8")


def logical_lines(text):
    return text.replace("\\\n", " ").splitlines()


def test_the_script_is_a_bash_script_with_one_parameter_reference():
    assert SCRIPT_TEXT.startswith("#!/bin/bash\n")
    assert SCRIPT_TEXT.count("{{") == 1 and "DIGEST_PARAM='{{ digest }}'\n" in SCRIPT_TEXT
    assert len(SCRIPT_TEXT.encode()) < 60_000  # the document's limit is 64 KB


def test_the_script_names_no_account_number_arn_secret_or_real_address():
    assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])", SCRIPT_TEXT)
    assert not re.search(r"(?<![A-Za-z])arn:", SCRIPT_TEXT)
    assert not re.search(r"AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|-----BEGIN|eyJ[A-Za-z0-9_-]{7,}\.", SCRIPT_TEXT)
    allowed = {"127.0.0.1", "169.254.169.254"}  # loopback, and the instance metadata service's fixed address
    for address in re.findall(r"(?<![0-9.])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9])", SCRIPT_TEXT):
        assert address in allowed or address.startswith("10.231.77."), address


def test_the_script_runs_no_sql_no_make_no_tracing_and_sets_no_database_url():
    for word in ("make migrate", "socboard.migrate", "psql", "GRANT", "ALTER ROLE", "SOCBOARD_DATABASE_URL", "set -x", "xtrace", "compose config", "--profile", ":latest"):
        assert word not in SCRIPT_TEXT, word


def test_the_script_never_touches_caddy_or_system_settings():
    assert "/etc/caddy" not in SCRIPT_TEXT
    for match in re.finditer(r"systemctl\s+(\S+)", SCRIPT_TEXT):
        assert match.group(1) == "is-active", match.group(0)
    for word in ("ufw", "iptables", "useradd", "usermod", "sysctl", "chown", "visudo", "crontab", "FORWARDED_ALLOW_IPS=", "reload", "docker restart", "compose restart"):
        assert word not in SCRIPT_TEXT, word


def test_every_compose_up_and_run_never_pulls_and_every_up_never_builds():
    lines = logical_lines(SCRIPT_TEXT)
    for line in lines:
        if re.search(r"\bcompose up\b|\bcompose run\b|\bdocker run\b", line):
            assert "--pull never" in line, line
        if re.search(r"\bcompose up\b", line):
            assert "--no-build" in line, line
    # `compose run` has no --no-build option (Compose 2): it builds only a missing image, and --pull never plus the pulled image avoid that.
    assert sum(1 for line in lines if re.search(r"\bcompose (up|run)\b", line)) >= 5


def test_the_image_is_always_the_fixed_repository_by_digest():
    assert "IMAGE_REPO='ghcr.io/andrew-chung-au/socboard'" in SCRIPT_TEXT
    assert 'IMAGE="$IMAGE_REPO@$digest"' in SCRIPT_TEXT
    assert "docker pull \"$IMAGE\"" in SCRIPT_TEXT
    assert not re.search(r"docker pull (?!\"\$IMAGE\")", SCRIPT_TEXT)


def test_docker_inspect_always_has_a_format_and_logs_have_one_form():
    for line in logical_lines(SCRIPT_TEXT):
        if "inspect" in line and "docker" in line and not line.lstrip().startswith("#"):
            assert "--format" in line, line
    code = "\n".join(line for line in logical_lines(SCRIPT_TEXT) if not line.lstrip().startswith("#"))
    assert code.count("docker logs") == 1 and 'docker logs --tail 20 "$1"' in code


def test_the_hostname_is_derived_as_main_tf_derives_it(box):
    rule = re.search(r'hostname\s*=\s*"\$\{replace\(aws_eip\.server\.public_ip, "\.", "-"\)\}(\.[a-z.]+)"', MAIN_TF.read_text(encoding="utf-8"))
    assert rule, "main.tf no longer derives its hostname the way this test reads it"
    ip = "203.0.113.77"
    r = box.run(env={"STUB_IP": ip})
    expected = ip.replace(".", "-") + rule.group(1)
    health = [line for line in r.calls if line.startswith("curl") and "/api/health" in line]
    assert len(health) == 1
    assert f"--resolve {expected}:443:127.0.0.1" in health[0] and health[0].endswith(f"https://{expected}/api/health")


def test_the_terraform_files_match_what_the_script_expects():
    document = (ROOT / "terraform" / "server" / "deploy_document.tf").read_text(encoding="utf-8")
    assert 'resource "aws_ssm_document" "deploy"' in document and 'name            = "socboard-deploy"' in document
    assert 'file("${path.module}/socboard-deploy.sh")' in document
    assert 'allowedPattern = "^sha256:[0-9a-f]{64}$"' in document
    assert document.count("type           =") == 1 and "parameters = {" in document
    tftest = (ROOT / "terraform" / "server" / "tests" / "server.tftest.hcl").read_text(encoding="utf-8")
    assert 'run "deploy_document"' in tftest


# --- the digest ---------------------------------------------------------------------------------

BAD_DIGESTS = [
    "latest",
    "sha256:" + "a" * 63,
    "sha256:" + "a" * 65,
    "sha256:" + "A" * 64,
    "sha256:" + "a" * 64 + "\n",
    "sha256:" + "a" * 64 + ";id",
    "sha256:$(id)",
    "sha256:" + "a" * 63 + " ",
    "sha256:" + "a" * 63 + "g",
    "SHA256:" + "a" * 64,
    "a" * 64,
    f"{REPO}:20261115-abcdef0",
    "",
]


@pytest.mark.parametrize("bad", BAD_DIGESTS)
def test_a_bad_digest_exits_2_before_any_call_and_changes_nothing(box, bad):
    r = box.run(bad)
    assert_only(r, 2, FIXED[2])
    assert set(r.kinds()) <= {"flock"}, r.kinds()
    assert [p.name for p in box.files_left()] == ["lock"]
    assert_safe(r)


def test_no_argument_leaves_the_parameter_reference_which_is_refused(box):
    r = box.run(use_default_arg=False)
    assert_only(r, 2, FIXED[2])
    assert set(r.kinds()) <= {"flock"}


def test_a_command_in_the_digest_is_never_run(box):
    marker = box.base / "marker"
    r = box.run(f"sha256:{'a' * 64};touch {marker}")
    assert r.code == 2 and not marker.exists()


# --- the lock ---------------------------------------------------------------------------------------


def test_a_second_run_while_one_holds_the_lock_exits_8_and_changes_nothing(box):
    box.run_dir.mkdir(parents=True)
    with open(box.run_dir / "lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = box.run()
    assert_only(r, 8, FIXED[8])
    assert r.kinds() == ["flock"]
    assert [p.name for p in box.files_left()] == ["lock"]


# --- already deployed --------------------------------------------------------------------------------


def test_the_same_digest_running_healthy_is_already_deployed_and_nothing_else_runs(box):
    box.set_app(IMAGE)
    r = box.run()
    assert_only(r, 0, ALREADY)
    assert r.kinds() == ["flock", "inspect-image", "inspect-health"]
    assert [p.name for p in box.files_left()] == ["lock"]
    assert_safe(r)


def test_the_same_digest_running_unhealthy_continues_as_a_normal_deploy(box):
    box.set_app(IMAGE, health="unhealthy")
    r = box.run()
    assert_only(r, 0, DEPLOYED)
    assert "pull" in r.kinds() and "up-app" in r.kinds()


# --- a good run ---------------------------------------------------------------------------------------

SUCCESS_ORDER = [
    "flock", "inspect-image", "rm-cand", "pull", "label-source", "label-revision", "fetch-compose", "fetch-db-init",
    "boto3-check", "secret-read", "up-db", "network", "run-db-init", "run-migrate", "run-seed", "run-candidate",
    "inspect-running", "inspect-health", "rm-cand", "up-app", "inspect-running", "inspect-health", "smoke",
    "caddy-active", "imds-token", "imds-ip", "https-health",
]


def test_a_good_run_calls_things_in_the_documented_order_and_prints_one_line(box):
    r = box.run()
    assert_only(r, 0, DEPLOYED)
    assert r.kinds() == SUCCESS_ORDER
    assert_safe(r)


def test_a_good_run_leaves_the_release_files_and_the_current_link(box):
    r = box.run()
    release = box.opt / "releases" / f"{REVISION}-{'a' * 12}"
    assert (release / "compose.yaml").is_file() and (release / "compose" / "db_init.py").is_file()
    assert not list(release.rglob("*.part"))
    assert os.readlink(box.opt / "current") == str(release)
    override = (release / "deploy.override.yaml").read_text(encoding="utf-8")
    assert "SOCBOARD_TRUSTED_PROXIES: 10.231.77.1\n" in override
    assert override.count("restart: unless-stopped") == 2 and "FORWARDED_ALLOW_IPS" not in override and "proxy:" not in override
    assert r.code == 0


def test_the_variable_file_holds_the_eight_values_and_two_fixed_ones_and_nothing_else(box):
    box.run()
    lines = (box.state / "vars-snapshot").read_text(encoding="utf-8").splitlines()
    expected = [f"{name}={value}" for name, value in SECRET_VALUES.items()] + [f"STACK_IMAGE={IMAGE}", "STACK_PGDATA_DIR=/srv/socboard-data"]
    assert sorted(lines) == sorted(expected)


def test_the_compose_commands_have_the_documented_shape(box):
    r = box.run()
    compose = [line for line in r.calls if line.startswith("docker compose")]
    tails = [" ".join(compose_tail(line)) for line in compose]
    assert "up -d --no-deps --wait --wait-timeout 120 --pull never --no-build db" in tails
    for service in ("db-init", "migrate", "seed"):
        assert f"run --rm --no-deps --pull never {service}" in tails
    assert f"run --no-deps -d --pull never --name {CAND} -p 127.0.0.1:8001:8000 app" in tails
    assert "up -d --no-deps --pull never --no-build app" in tails
    for line in compose:
        assert "--env-file" in line and "deploy.override.yaml" in line and "proxy" not in line.replace("SOCBOARD_TRUSTED_PROXIES", "")
    assert all(re.search(r"up .* (db|app)$", t) for t in tails if t.startswith("up "))
    assert "--rm" not in [t for t in tails if "--name" in t][0].split()


def test_the_smoke_check_gets_two_values_by_name_only_on_the_host_network(box):
    r = box.run()
    smoke = [line for line in r.calls if line.startswith("docker run")][0]
    assert smoke == f"docker run --rm --pull never --network host -e SOCBOARD_SEED_PASSWORD_LEAD -e SOCBOARD_INGEST_API_KEY {IMAGE} python -m socboard.smoke http://127.0.0.1:8000"
    assert "SMOKE_ENV ok" in r.calls


def test_the_check_through_caddy_resolves_to_loopback_and_does_not_print_the_body(box):
    r = box.run()
    health = [line for line in r.calls if "/api/health" in line][0]
    assert "--resolve 203-0-113-10.sslip.io:443:127.0.0.1" in health and "--proto =https" in health
    assert "BODYCANARY" not in r.out
    token = [line for line in r.calls if line.startswith("curl") and "public-ipv4" in line][0]
    assert "--config -" in token and TOKEN not in token


def test_a_missing_boto3_is_installed_from_the_archive_once(box):
    r = box.run(env={"STUB_BOTO3_MISSING": "1"})
    assert_only(r, 0, DEPLOYED)
    assert "apt-get install -y python3-boto3" in r.calls
    assert r.kinds().index("apt") < r.kinds().index("secret-read")


def test_an_installed_boto3_runs_no_apt_get(box):
    assert "apt" not in box.run().kinds()


# --- the refused cases ---------------------------------------------------------------------------------


def refused(r, code, text):
    assert_only(r, code, text)
    assert_old_app_untouched(r)
    assert "logs" not in r.kinds()
    assert_candidate_removed(r)
    assert_safe(r)


def test_a_failed_pull_exits_4_and_the_current_release_keeps_serving(box):
    box.set_app(PREVIOUS_IMAGE)
    r = box.run(env={"STUB_PULL_FAIL": "1"})
    refused(r, 4, FIXED[4])
    assert "fetch-compose" not in r.kinds()


@pytest.mark.parametrize(
    "env",
    [
        {"STUB_SOURCE": "https://github.com/someone-else/socboard"},
        {"STUB_SOURCE": ""},
        {"STUB_REVISION": "0123456789abcdef0123456789abcdef0123456"},
        {"STUB_REVISION": "0123456789ABCDEF0123456789abcdef01234567"},
        {"STUB_REVISION": "0123456789abcdef0123456789abcdef012345678"},
        {"STUB_REVISION": ""},
        {"STUB_REVISION": "../../../0123456789abcdef0123456789abcdef"},
    ],
)
def test_a_wrong_label_exits_4_before_any_file_is_fetched(box, env):
    r = box.run(env=env)
    refused(r, 4, FIXED[4])
    assert not {"fetch-compose", "secret-read", "up-db"} & set(r.kinds())


@pytest.mark.parametrize("env", [{"STUB_FETCH_FAIL": "1"}, {"STUB_FETCH_EMPTY": "1"}])
def test_files_that_cannot_be_fetched_exit_4_and_leave_no_partial_file(box, env):
    r = box.run(env=env)
    refused(r, 4, FIXED[4])
    assert not [p for p in box.files_left() if p.suffix == ".part"]
    assert "secret-read" not in r.kinds()


def good(**changes):
    data = dict(SECRET_VALUES)
    data.update(changes)
    return data


BAD_SECRETS = {
    "not json": "this is not json DENIEDCANARY",
    "a list": json.dumps(["a", "b"]),
    "a string": json.dumps("abc"),
    "a missing key": {k: v for k, v in SECRET_VALUES.items() if k != "STACK_PG_APP_PASSWORD"},
    "an extra key": good(EXTRA_KEY="abc"),
    "an empty value": good(SOCBOARD_TOKEN_SECRET=""),
    "a number": good(SOCBOARD_INGEST_API_KEY=12345678901234567890),
    "a null": good(SOCBOARD_INGEST_API_KEY=None),
    "a dollar sign": good(SOCBOARD_TOKEN_SECRET="canary$HOME0123456789"),
    "a double quote": good(SOCBOARD_TOKEN_SECRET='canary"quote0123456789'),
    "a single quote": good(SOCBOARD_TOKEN_SECRET="canary'quote0123456789"),
    "a hash": good(SOCBOARD_TOKEN_SECRET="canary#hash0123456789"),
    "a space": good(SOCBOARD_TOKEN_SECRET="canary space0123456789"),
    "a newline": good(SOCBOARD_TOKEN_SECRET="canary\nnewline0123456789"),
    "a backslash": good(SOCBOARD_TOKEN_SECRET="canary\\back0123456789"),
    "a semicolon": good(SOCBOARD_SEED_PASSWORD_LEAD="canary;semi0123456789"),
    "a non-ASCII letter": good(SOCBOARD_SEED_PASSWORD_LEAD="canar\u00e9y0123456789"),
    "a database password with a plus": good(STACK_PG_OWNER_PASSWORD="canary+owner0002"),
    "a database password with a dash": good(STACK_PG_APP_PASSWORD="canary-app0003"),
    "a database password with a slash": good(STACK_PG_SUPERUSER_PASSWORD="canary/super0001"),
}


@pytest.mark.parametrize("name", list(BAD_SECRETS))
def test_a_secret_that_is_not_usable_exits_3_naming_nothing(box, name):
    box.set_app(PREVIOUS_IMAGE)
    box.write_secret(BAD_SECRETS[name])
    r = box.run()
    refused(r, 3, FIXED[3])
    assert not {"up-db", "run-seed"} & set(r.kinds())


def test_a_secret_that_cannot_be_read_exits_3_and_prints_no_error_text(box):
    r = box.run(env={"STUB_SECRET_RAISE": "DENIEDCANARY"})
    refused(r, 3, FIXED[3])


def test_a_secret_with_every_allowed_character_is_accepted(box):
    box.write_secret(good(SOCBOARD_TOKEN_SECRET="aZ09._~+/=@%:,-aZ09._~+/=@%:,-xyz"))
    assert box.run().code == 0


def test_a_boto3_that_cannot_be_installed_exits_3(box):
    r = box.run(env={"STUB_BOTO3_MISSING": "1", "STUB_APT_RC": "100"})
    refused(r, 3, FIXED[3])
    assert "secret-read" not in r.kinds()


@pytest.mark.parametrize("service", ["db-init", "migrate", "seed"])
def test_a_failed_one_shot_service_exits_5_and_runs_nothing_after_it(box, service):
    box.set_app(PREVIOUS_IMAGE)
    r = box.run(env={"STUB_FAIL_SERVICE": service})
    refused(r, 5, FIXED[5])
    order = ["run-db-init", "run-migrate", "run-seed"]
    assert r.kinds()[-1] == f"run-{service}"
    assert not set(order[order.index(f"run-{service}") + 1 :]) & set(r.kinds())
    assert "run-candidate" not in r.kinds()


def test_a_database_that_does_not_start_exits_5(box):
    r = box.run(env={"STUB_DB_UP_FAIL": "1"})
    refused(r, 5, FIXED[5])
    assert "run-db-init" not in r.kinds()


@pytest.mark.parametrize("gateway", ["10.231.77.128", "10.231.77.2", "", "10.231.77.1 10.231.77.2"])
def test_another_gateway_exits_10_before_any_migration(box, gateway):
    box.set_app(PREVIOUS_IMAGE)
    r = box.run(env={"STUB_GATEWAY": gateway})
    refused(r, 10, FIXED[10])
    assert not {"run-db-init", "run-candidate"} & set(r.kinds())
    assert r.kinds()[-1] == "network"


def test_a_network_that_cannot_be_read_exits_10(box):
    r = box.run(env={"STUB_NETWORK_FAIL": "1"})
    refused(r, 10, FIXED[10])


# --- the candidate (exit 6) and the log lines ------------------------------------------------------------


def numbered(n=30):
    return [f"LOG-L{i:02d}" for i in range(1, n + 1)]


@pytest.mark.parametrize(
    "env",
    [{"STUB_CAND_RUN_FAIL": "1"}, {"STUB_CAND_RUNNING": "false"}, {"STUB_CAND_HEALTH": "starting"}, {"STUB_CAND_HEALTH": "unhealthy"}],
)
def test_a_candidate_that_fails_exits_6_with_the_last_20_log_lines_and_the_old_app_keeps_serving(box, env):
    box.set_app(PREVIOUS_IMAGE)
    box.set_logs(CAND, numbered())
    r = box.run(env=env)
    assert r.code == 6
    assert r.lines() == [FIXED[6]] + numbered()[10:]
    assert_old_app_untouched(r)
    assert not {"up-app", "smoke"} & set(r.kinds())
    assert_candidate_removed(r)
    assert_safe(r)


def test_the_log_is_read_before_the_candidate_is_removed(box):
    box.set_logs(CAND, numbered(3))
    r = box.run(env={"STUB_CAND_HEALTH": "starting"})
    kinds = r.kinds()
    assert kinds.index("logs") < len(kinds) - 1 - kinds[::-1].index("rm-cand")
    assert [line for line in r.calls if line.startswith("docker logs")] == [f"docker logs --tail 20 {CAND}"]


def canary_lines():
    lines = [f"seen {value} here" for value in SECRET_VALUES.values()]
    lines.append(f"two {SECRET_VALUES['SOCBOARD_TOKEN_SECRET']} and {SECRET_VALUES['STACK_PG_APP_PASSWORD']} on one line")
    return lines


def test_a_candidate_log_with_every_secret_value_prints_none_of_them(box):
    box.set_logs(CAND, canary_lines())
    r = box.run(env={"STUB_CAND_HEALTH": "starting"})
    assert r.code == 6 and r.lines()[0] == FIXED[6]
    assert r.out.count("[redacted]") == 10
    assert_safe(r)


def test_a_value_inside_a_longer_value_leaves_no_tail_of_the_longer_one(box):
    box.write_secret(good(SOCBOARD_INGEST_API_KEY="canaryShared00001", SOCBOARD_SEED_PASSWORD_ANALYST_A="canaryShared00001LONGTAILxyz"))
    box.set_logs(CAND, ["x canaryShared00001LONGTAILxyz y canaryShared00001 z"])
    r = box.run(env={"STUB_CAND_HEALTH": "starting"})
    assert r.lines() == [FIXED[6], "x [redacted] y [redacted] z"]


# --- after the swap (exits 7 and 9) ------------------------------------------------------------------------

POST_SWAP_FAILURES = {
    "the new container is unhealthy": {"STUB_NEW_HEALTH": "unhealthy"},
    "the swap command fails": {"STUB_UP_APP_FAIL": "1"},
    "the smoke check fails": {"STUB_SMOKE_RC": "1"},
    "caddy is not active": {"STUB_CADDY_RC": "3"},
    "the metadata service does not answer": {"STUB_IMDS_FAIL": "1"},
    "the check through caddy does not answer 200": {"STUB_HEALTH_CODE": "502"},
    "the address is not an address": {"STUB_IP": "not-an-address"},
}


@pytest.mark.parametrize("name", list(POST_SWAP_FAILURES))
def test_a_failure_after_the_swap_restores_the_previous_image_and_exits_7(box, name):
    box.set_app(PREVIOUS_IMAGE)
    previous = box.previous_release()
    box.set_logs(APP, canary_lines())
    r = box.run(env=POST_SWAP_FAILURES[name])
    assert r.code == 7 and r.lines()[0] == FIXED["7r"]
    assert r.out.count("[redacted]") == 10
    assert_safe(r)
    restore = [i for i, line in enumerate(r.calls) if line.startswith("IMG ") and line != "IMG unset"]
    assert [r.calls[i] for i in restore] == [f"IMG {PREVIOUS_IMAGE}"]
    assert str(previous / "compose.yaml") in r.calls[restore[0] - 2]  # the restore uses the previous release's files
    assert r.kinds()[-1] in {"up-app", "inspect-health"}
    assert r.kinds().count("up-app") == 2
    assert (box.opt / "current").resolve() == previous.resolve()  # `current` still names the previous release


def test_the_previous_image_is_restored_with_the_same_swap_command(box):
    box.set_app(PREVIOUS_IMAGE)
    r = box.run(env={"STUB_NEW_HEALTH": "unhealthy"})
    ups = [line for line in r.calls if line.startswith("docker compose") and line.endswith(" app") and " up " in line]
    assert len(ups) == 2 and all(line.endswith("up -d --no-deps --pull never --no-build app") for line in ups)


def test_the_new_container_logs_are_the_last_20_lines_and_the_log_is_read_before_the_restore(box):
    box.set_app(PREVIOUS_IMAGE)
    box.set_logs(APP, numbered())
    r = box.run(env={"STUB_NEW_HEALTH": "unhealthy"})
    assert r.lines() == [FIXED["7r"]] + numbered()[10:]
    kinds = r.kinds()
    assert kinds.index("logs") < len(kinds) - 1 - kinds[::-1].index("up-app")
    assert [line for line in r.calls if line.startswith("docker logs")] == [f"docker logs --tail 20 {APP}"]


def test_a_failure_after_the_first_deploy_exits_7_with_no_previous_release_and_leaves_db_running(box):
    box.set_logs(APP, numbered(5))
    r = box.run(env={"STUB_NEW_HEALTH": "unhealthy"})
    assert r.lines() == [FIXED["7n"]] + numbered(5)
    assert r.code == 7
    assert r.kinds().count("up-app") == 1 and r.kinds().count("stop-app") == 1
    assert not {"stop-other", "rm-other"} & set(r.kinds()) and not [k for k in r.kinds() if k.startswith("compose-")]
    assert not (box.opt / "current").exists()
    assert_safe(r)


def test_an_app_that_is_not_ours_is_not_restored(box):
    box.set_app("example.invalid/other@sha256:" + "c" * 64)
    r = box.run(env={"STUB_NEW_HEALTH": "unhealthy"})
    assert r.lines()[0] == FIXED["7n"] and r.kinds().count("up-app") == 1


def test_a_restore_that_fails_exits_9_with_the_logs(box):
    box.set_app(PREVIOUS_IMAGE)
    box.set_logs(APP, canary_lines())
    r = box.run(env={"STUB_NEW_HEALTH": "unhealthy", "STUB_RESTORE_HEALTH": "unhealthy"})
    assert r.code == 9 and r.lines()[0] == FIXED[9]
    assert r.out.count("[redacted]") == 10
    assert_safe(r)


def test_a_restore_whose_swap_command_fails_exits_9(box):
    box.set_app(PREVIOUS_IMAGE)
    r = box.run(env={"STUB_UP_APP_FAIL": "all"})
    assert r.code == 9 and r.lines() == [FIXED[9]]


def test_the_new_container_log_has_the_last_20_lines_for_exit_9(box):
    box.set_app(PREVIOUS_IMAGE)
    box.set_logs(APP, numbered())
    r = box.run(env={"STUB_NEW_HEALTH": "unhealthy", "STUB_RESTORE_HEALTH": "unhealthy"})
    assert r.lines() == [FIXED[9]] + numbered()[10:]


# --- every exit cleans up and prints nothing else -------------------------------------------------------------


def test_the_check_through_caddy_prints_no_response_body_on_failure(box):
    box.set_app(PREVIOUS_IMAGE)
    r = box.run(env={"STUB_HEALTH_CODE": "500"})
    assert "BODYCANARY" not in r.out and r.lines() == [FIXED["7r"]]


@pytest.mark.parametrize(
    "env, code",
    [
        ({"STUB_PULL_FAIL": "1"}, 4),
        ({"STUB_FAIL_SERVICE": "seed"}, 5),
        ({"STUB_CAND_HEALTH": "starting"}, 6),
        ({"STUB_NEW_HEALTH": "unhealthy"}, 7),
        ({"STUB_GATEWAY": "10.231.77.128"}, 10),
        ({}, 0),
    ],
)
def test_every_exit_removes_the_variable_file_and_the_candidate_and_releases_the_lock(box, env, code):
    r = box.run(env=env)
    assert r.code == code
    assert_candidate_removed(r)
    assert_safe(r)
    assert not [p for p in box.run_dir.iterdir() if p.name != "lock"]


# --- the human's task file ---------------------------------------------------------------------------------


def task_file():
    found = sorted((ROOT / "_human-tasks").glob("*-human-task-237-engineer-first-deploy.md"))
    assert len(found) == 1
    return found[0].read_text(encoding="utf-8")


def test_the_task_file_has_no_inline_secret_no_real_address_and_every_result_row_not_checked():
    text = task_file()
    for line in text.splitlines():
        if "--secret-string" in line:
            assert "--secret-string file://" in line, line
        if "put-secret-value" in line:
            assert "--secret-string file://" in line, line
    for address in re.findall(r"(?<![0-9.])[0-9]{1,3}(?:\.[0-9]{1,3}){3}(?![0-9])", text):
        assert address == "127.0.0.1" or address.startswith(("10.231.77.", "203.0.113.")), address
    assert not re.search(r"(?<![0-9])[0-9]{12}(?![0-9])|(?<![A-Za-z])arn:", text)
    table = text.split("| Step | Pass or fail | Output or note |")[1].split("## Risk")[0]
    rows = [line for line in table.splitlines() if line.startswith("|") and not line.startswith("|---")]
    assert len(rows) >= 10 and all(line.split("|")[2].strip() == "NOT CHECKED" for line in rows), rows
