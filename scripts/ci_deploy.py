"""The logic of the dev deploy workflow (#238, D22a), kept in a script so tests can run it.

`find` decides from the publish run's artifact whether there is a digest to deploy. `deploy` sends the
Systems Manager document `socboard-deploy` with that digest, waits for the result, reads it by the script's
fixed lines (#237), checks the public health URL and writes the record that the promote workflow reads.

The workflow holds the deploy role while this runs, so: no environment value is printed, no output of the
server is printed except the script's own fixed lines, and every value from the workflow is checked against
a pattern before it is used. It uses only the standard library and the `aws` command line.
"""

import argparse
import datetime
import http.client
import json
import os
import re
import subprocess
import sys
import time

REGION = "us-east-1"
DOCUMENT = "socboard-deploy"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
TAG = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{7}")
REVISION = re.compile(r"[0-9a-f]{40}")
INSTANCE_ID = re.compile(r"i-[0-9a-f]{8,17}")
URL = re.compile(r"https://[A-Za-z0-9.-]+")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*")
RUN_ID = re.compile(r"[0-9]{1,20}")
RUN_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]{1,20}")
ARTIFACT_KEYS = {"schema", "image", "tag", "digest", "revision"}
RECORD_KEYS = (
    "schema", "environment", "result", "image", "digest", "tag", "revision", "deployed_at", "run_id", "run_url", "command_id",
)

POLL_SECONDS = 5
AWS_CALL_SECONDS = 60
POLL_LIMIT_SECONDS = 20 * 60
NOT_YET_SECONDS = 60
HEALTH_TRIES = 6
HEALTH_WAIT_SECONDS = 10
HEALTH_TIMEOUT_SECONDS = 10
RUNNING = {"Pending", "InProgress", "Delayed"}

DEPLOYED = "socboard-deploy: deployed."
ALREADY = "socboard-deploy: already deployed. Nothing was changed."
# The script's fixed lines, by exit code (#237's failure table, and terraform/server/socboard-deploy.sh's
# text_for, which tests/test_ci_deploy.py compares with this table once that file is on main). Only these
# lines are ever printed from the server's output.
FAILURE_LINES = {
    2: (
        "socboard-deploy: refused: the digest must be sha256: followed by 64 lower-case hex digits. Nothing was changed.",
        "socboard-deploy: refused: the environment must be dev or prod. Nothing was changed.",
    ),
    3: ("socboard-deploy: the application secret could not be read or is not usable. The current release was not changed.",),
    4: ("socboard-deploy: the image, the database image or the Compose files could not be fetched, or are not this repository's. The current release was not changed.",),
    5: ("socboard-deploy: db-init, migrate or seed failed. The current release was not changed.",),
    6: ("socboard-deploy: the new app container did not become healthy. The current release was not changed.",),
    7: (
        "socboard-deploy: the new release failed after the swap; the previous release was restored.",
        "socboard-deploy: the new release failed after the swap; there was no previous release to restore.",
    ),
    8: ("socboard-deploy: another run is in progress. Nothing was changed.",),
    9: ("socboard-deploy: the new release failed after the swap and the previous release could not be restored.",),
    10: ("socboard-deploy: the stack network's gateway is not the configured proxy address. The current release was not changed.",),
}
FAILURES = {line for lines in FAILURE_LINES.values() for line in lines}
KNOWN_LINES = FAILURES | {DEPLOYED, ALREADY}


class Refused(Exception):
    """A value or an answer that stops the run. The message names the field, never the value."""


# --- find ----------------------------------------------------------------------------------------


def check_artifact(text, repository, head_sha):
    """The published-image artifact as a dict, or Refused naming the field."""
    try:
        data = json.loads(text)
    except ValueError:
        raise Refused("the published-image artifact is not JSON") from None
    if not isinstance(data, dict):
        raise Refused("the published-image artifact is not one JSON object")
    if set(data) != ARTIFACT_KEYS:
        raise Refused("the published-image artifact does not have exactly the keys " + ", ".join(sorted(ARTIFACT_KEYS)))
    if data["schema"] != 1 or isinstance(data["schema"], bool):
        raise Refused("the published-image artifact's schema is not 1")
    for key in ("image", "tag", "digest", "revision"):
        if not isinstance(data[key], str):
            raise Refused(f"the published-image artifact's {key} is not a string")
    if data["image"] != f"ghcr.io/{repository.lower()}":
        raise Refused("the published-image artifact's image is not this repository's")
    if not TAG.fullmatch(data["tag"]):
        raise Refused("the published-image artifact's tag is malformed")
    if not DIGEST.fullmatch(data["digest"]):
        raise Refused("the published-image artifact's digest is malformed")
    if not REVISION.fullmatch(data["revision"]):
        raise Refused("the published-image artifact's revision is malformed")
    if data["revision"] != head_sha.lower():
        raise Refused("the published-image artifact's revision is not the publish run's commit")
    return data


def find(args, out):
    repository, head_sha = args.repository, args.head_sha
    if not REVISION.fullmatch(head_sha.lower()):
        raise Refused("the publish run's commit is malformed")
    if not args.file or not os.path.exists(args.file):
        print("this publish run published nothing")
        print("deploy=false", file=out)
        return 0
    with open(args.file, encoding="utf-8") as handle:
        data = check_artifact(handle.read(), repository, head_sha)
    if not REVISION.fullmatch(args.main_head.lower()):
        raise Refused("the head of main could not be read")
    if args.main_head.lower() != data["revision"]:
        print("superseded: main has moved on; a later publish deploys")
        print("deploy=false", file=out)
        return 0
    print("deploy=true", file=out)
    for key in ("image", "tag", "digest", "revision"):
        print(f"{key}={data[key]}", file=out)
    print(f"to deploy: {data['tag']}")
    return 0


# --- deploy --------------------------------------------------------------------------------------


def run_aws(arguments):
    """One `aws` call. Returns (exit code, stdout text, stderr text); nothing is printed."""
    try:
        done = subprocess.run(["aws", *arguments], capture_output=True, text=True, check=False, timeout=AWS_CALL_SECONDS)
    except FileNotFoundError:
        raise Refused("the aws command line is not installed on the runner") from None
    except subprocess.TimeoutExpired:
        raise Refused(f"aws {arguments[1]} did not answer within {AWS_CALL_SECONDS} seconds") from None
    return done.returncode, done.stdout, done.stderr


def check_inputs(args, env):
    for name, value, pattern in (
        ("--digest", args.digest, DIGEST), ("--tag", args.tag, TAG), ("--revision", args.revision, REVISION),
        ("--image", args.image, IMAGE), ("--run-id", args.run_id, RUN_ID), ("--run-url", args.run_url, RUN_URL),
    ):
        if not pattern.fullmatch(value or ""):
            raise Refused(f"{name} is missing or malformed")
    return check_environment(env)


def check_environment(env):
    instance = env.get("DEPLOY_INSTANCE_ID", "")
    if not instance:
        raise Refused("DEPLOY_INSTANCE_ID is not set on the deploy Environment")
    if not INSTANCE_ID.fullmatch(instance):
        raise Refused("DEPLOY_INSTANCE_ID is not an instance id (i- and 8 to 17 lower-case hex digits)")
    url = env.get("DEPLOY_URL", "")
    if not url:
        raise Refused("DEPLOY_URL is not set on the deploy Environment")
    if not URL.fullmatch(url) or url.endswith(("/", ".", "-")) or url == "https://":
        raise Refused("DEPLOY_URL is not https:// and a host name only")
    return instance, url


def send(instance, digest, revision, environment="dev"):
    # A dev run sends the digest alone, exactly as before the document had a second parameter (#267), so the
    # dev call is unchanged. A prod run (the promote workflow, #269) names the environment as well: the
    # document's second parameter. VERIFY: that the JSON form of --parameters is accepted for a document with
    # two parameters (the first prod promote shows it; a refusal is "aws ssm send-command failed").
    if environment == "dev":
        parameters = f"digest={digest}"
    elif environment == "prod":
        parameters = json.dumps({"digest": [digest], "environment": ["prod"]}, separators=(",", ":"))
    else:
        raise Refused("the environment must be dev or prod")
    code, stdout, _ = run_aws(
        ["ssm", "send-command", "--region", REGION, "--document-name", DOCUMENT, "--instance-ids", instance,
         "--parameters", parameters, "--comment", f"socboard deploy {revision[:7]}", "--output", "json"]
    )
    if code != 0:
        raise Refused(f"aws ssm send-command failed (exit {code}); the instance id in DEPLOY_INSTANCE_ID may be stale")
    try:
        command_id = json.loads(stdout)["Command"]["CommandId"]
    except (ValueError, KeyError, TypeError):
        raise Refused("aws ssm send-command did not answer with a command id") from None
    if not isinstance(command_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", command_id):
        raise Refused("aws ssm send-command answered with a malformed command id")
    return command_id


def wait(command_id, instance, clock, sleep):
    """The finished invocation as a dict. Raises Refused on a timeout or an unreadable answer."""
    started = clock()
    while True:
        code, stdout, stderr = run_aws(
            ["ssm", "get-command-invocation", "--region", REGION, "--command-id", command_id, "--instance-id", instance, "--output", "json"]
        )
        age = clock() - started
        if code != 0:
            if "InvocationDoesNotExist" in stderr and age < NOT_YET_SECONDS:
                sleep(POLL_SECONDS)
                continue
            raise Refused(f"aws ssm get-command-invocation failed (exit {code})")
        try:
            invocation = json.loads(stdout)
            status = invocation["Status"]
        except (ValueError, KeyError, TypeError):
            raise Refused("aws ssm get-command-invocation gave an unreadable answer") from None
        if status not in RUNNING:
            return invocation
        if age >= POLL_LIMIT_SECONDS:
            raise Refused("timed out waiting; the command may still be running on the server")
        sleep(POLL_SECONDS)


def output_lines(invocation, key):
    text = invocation.get(key)
    return text.splitlines() if isinstance(text, str) else []


def fixed_lines(invocation):
    """The script's own fixed lines in the invocation's output, exactly as written, and nothing else from it."""
    return [
        line
        for key in ("StandardOutputContent", "StandardErrorContent")
        for line in output_lines(invocation, key)
        if line in KNOWN_LINES
    ]


def read_result(invocation):
    """('deployed' | 'already deployed', lines) or Refused with what to read; prints only fixed text.

    A pass needs status Success, response code 0 (an integer), the deployed or already-deployed line on the
    script's standard output, and no failure line anywhere.
    """
    status = invocation.get("Status")
    response = invocation.get("ResponseCode")
    lines = fixed_lines(invocation)
    for line in lines:
        print(line)
    code_ok = isinstance(response, int) and not isinstance(response, bool)
    if status != "Success" or not code_ok or response != 0:
        shown = response if code_ok else "unknown"
        known = " The script's fixed line is printed above." if lines else " No fixed line of the script was in the output."
        raise Refused(f"the command ended {status if isinstance(status, str) and status.isalpha() else 'unreadable'} with response code {shown}.{known}")
    if FAILURES & set(lines):
        raise Refused("the command succeeded but the script printed a failure line")
    stdout = output_lines(invocation, "StandardOutputContent")
    if DEPLOYED in stdout:
        return "deployed", lines
    if ALREADY in stdout:
        return "already deployed", lines
    raise Refused("the command succeeded but did not report a deploy")


def http_status(url, timeout):
    """The status of GET <url>/api/health with no redirect followed and no body kept, or None."""
    host = url.removeprefix("https://")
    try:
        connection = http.client.HTTPSConnection(host, timeout=timeout)
        try:
            connection.request("GET", "/api/health", headers={"User-Agent": "socboard-ci-deploy"})
            return connection.getresponse().status
        finally:
            connection.close()
    except (OSError, http.client.HTTPException):
        return None


def check_health(url, sleep):
    last = None
    for attempt in range(HEALTH_TRIES):
        last = http_status(url, HEALTH_TIMEOUT_SECONDS)
        print(f"health check {attempt + 1} of {HEALTH_TRIES}: {last if last is not None else 'no answer'}")
        if last == 200:
            return
        if attempt + 1 < HEALTH_TRIES:
            sleep(HEALTH_WAIT_SECONDS)
    raise Refused(f"the public health check did not answer 200 (last: {last if last is not None else 'no answer'})")


def make_record(args, result, command_id, finished):
    record = {
        "schema": 1, "environment": "dev", "result": result, "image": args.image, "digest": args.digest, "tag": args.tag,
        "revision": args.revision, "deployed_at": finished, "run_id": args.run_id, "run_url": args.run_url, "command_id": command_id,
    }
    assert tuple(record) == RECORD_KEYS
    return record


def write_summary(path, record, health, line):
    rows = [
        ("Environment", record["environment"]), ("Result", record["result"]), ("Script's line", f"`{line}`"), ("Digest", f"`{record['digest']}`"),
        ("Tag", f"`{record['tag']}`"), ("Revision", f"`{record['revision']}`"), ("Command id", f"`{record['command_id']}`"),
        ("Health check", health), ("Run", record["run_url"]),
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("### Deploy to dev\n\n| | |\n|---|---|\n")
        for name, value in rows:
            handle.write(f"| {name} | {value} |\n")


def deploy(args, env, clock=time.monotonic, sleep=time.sleep, now=None):
    instance, url = check_inputs(args, env)
    command_id = send(instance, args.digest, args.revision)
    print(f"sent {DOCUMENT} for {args.tag} (command {command_id})")
    invocation = wait(command_id, instance, clock, sleep)
    finished = (now or (lambda: datetime.datetime.now(datetime.timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    result, _ = read_result(invocation)
    line = DEPLOYED if result == "deployed" else ALREADY
    check_health(url, sleep)
    record = make_record(args, result, command_id, finished)
    with open(args.record, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    summary = args.summary or env.get("GITHUB_STEP_SUMMARY")
    if summary:
        write_summary(summary, record, "200", line)
    print(f"dev is running {args.digest} ({result})")
    return 0


# --- command line --------------------------------------------------------------------------------


def parser():
    top = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = top.add_subparsers(dest="command", required=True)
    one = sub.add_parser("find")
    one.add_argument("--repository", required=True)
    one.add_argument("--head-sha", required=True)
    one.add_argument("--main-head", required=True)
    one.add_argument("--file", default="")
    one.add_argument("--output", default="", help="the file the step outputs go to ($GITHUB_OUTPUT); messages go to stdout")
    sub.add_parser("check", help="check DEPLOY_INSTANCE_ID and DEPLOY_URL before any role is assumed")
    two = sub.add_parser("deploy")
    for name in ("digest", "tag", "revision", "image", "run-id", "run-url", "record"):
        two.add_argument(f"--{name}", required=True)
    two.add_argument("--summary", default="")
    return top


def main(argv=None, env=None, out=None):
    args = parser().parse_args(argv)
    env = os.environ if env is None else env
    out = out or sys.stdout
    try:
        if args.command == "find":
            if args.output:
                with open(args.output, "a", encoding="utf-8") as handle:
                    return find(args, handle)
            return find(args, out)
        if args.command == "check":
            check_environment(env)
            print("DEPLOY_INSTANCE_ID and DEPLOY_URL are set and well formed")
            return 0
        return deploy(args, env)
    except Refused as refusal:
        print(f"ci_deploy: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
