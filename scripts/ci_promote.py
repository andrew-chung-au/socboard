"""The logic of the promote-to-prod workflow (#269): which digest goes to prod, and the deploy of it.

`resolve` runs in a job that holds no cloud role. It reads the deploy records dev left (the artifact
`deploy-record-dev`, #238), picks the digest (dev's newest, or the one named by the input if some dev record
holds it) and refuses anything else before an approval is asked. `deploy` runs in the approved job: it sends
the Systems Manager document `socboard-deploy` with that digest and `environment=prod`, reads the result by the
script's fixed lines, checks the public health URL and writes the record `deploy-record-prod`.

The `deploy` step holds the deploy role, so: no environment value is printed, no output of the server is
printed except the script's own fixed lines, and every value from the workflow is checked against a pattern
before it is used. Standard library only, apart from `scripts/ci_deploy.py` (whose patterns, `send`, `wait`,
`read_result` and `check_health` it reuses) and the `gh` and `aws` command lines.
"""

import argparse
import datetime
import io
import json
import os
import re
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ci_deploy as dev  # noqa: E402  (the dev deploy's patterns and AWS calls; one source for both)

Refused = dev.Refused

DEV_ARTIFACT = "deploy-record-dev"
PROD_ARTIFACT = "deploy-record-prod"
GH_CALL_SECONDS = 60
MAX_RECORDS = 30  # the newest dev records a rollback may name
LISTED_RECORDS = 10  # the newest dev records the summary lists
MAX_RECORD_BYTES = 64 * 1024
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
CREATED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]{8,12}Z")
DEPLOYED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
RESULTS = {"deployed", "already deployed"}
PROD_RECORD_KEYS = (*dev.RECORD_KEYS, "requested_by", "approved_by")
APPROVALS_UNREADABLE = "unreadable"


# --- reading GitHub ------------------------------------------------------------------------------


def run_gh(arguments, binary=False):
    """One `gh` call. Returns (exit code, stdout as text or bytes); nothing is printed."""
    try:
        done = subprocess.run(["gh", *arguments], capture_output=True, text=not binary, check=False, timeout=GH_CALL_SECONDS)
    except FileNotFoundError:
        raise Refused("the gh command line is not installed on the runner") from None
    except subprocess.TimeoutExpired:
        raise Refused(f"gh {arguments[1] if len(arguments) > 1 else ''} did not answer within {GH_CALL_SECONDS} seconds") from None
    return done.returncode, done.stdout


def list_artifacts(repository, name):
    """The non-expired artifacts called `name`, newest first (by created_at, then id), at most MAX_RECORDS."""
    code, stdout = run_gh(["api", f"repos/{repository}/actions/artifacts?name={name}&per_page=100"])
    if code != 0:
        raise Refused(f"the list of {name} artifacts could not be read")
    try:
        found = json.loads(stdout)["artifacts"]
        rows = []
        for item in found:
            if item.get("name") != name or item.get("expired") is not False:
                continue
            identifier, created = item["id"], item["created_at"]
            if isinstance(identifier, bool) or not isinstance(identifier, int) or not isinstance(created, str) or not CREATED_AT.fullmatch(created):
                raise ValueError
            rows.append((created, identifier))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Refused(f"the list of {name} artifacts is not in the expected shape") from None
    rows.sort(reverse=True)
    return [identifier for _, identifier in rows[:MAX_RECORDS]]


def fetch_record(repository, identifier, name):
    """The text of the one JSON file inside an artifact. VERIFY: `gh api .../zip` follows GitHub's redirect and prints the zip."""
    code, stdout = run_gh(["api", f"repos/{repository}/actions/artifacts/{identifier}/zip"], binary=True)
    if code != 0 or not stdout:
        raise Refused(f"a {name} artifact could not be downloaded")
    try:
        with zipfile.ZipFile(io.BytesIO(stdout)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) != 1 or members[0].file_size > MAX_RECORD_BYTES:
                raise Refused(f"a {name} artifact does not hold exactly one small file")
            return archive.read(members[0]).decode("utf-8")
    except (zipfile.BadZipFile, UnicodeDecodeError):
        raise Refused(f"a {name} artifact is not a zip of one text file") from None


# --- checking a record ---------------------------------------------------------------------------


def check_record(text, repository, environment, keys):
    """A deploy record as a dict, or Refused naming the field (never the value)."""
    label = f"a deploy-record-{environment} record"
    try:
        data = json.loads(text)
    except ValueError:
        raise Refused(f"{label} is not JSON") from None
    if not isinstance(data, dict):
        raise Refused(f"{label} is not one JSON object")
    if set(data) != set(keys):
        raise Refused(f"{label} does not have exactly the keys " + ", ".join(keys))
    if data["schema"] != 1 or isinstance(data["schema"], bool):
        raise Refused(f"{label}'s schema is not 1")
    if data["environment"] != environment:
        raise Refused(f"{label}'s environment is not {environment}")
    if data["result"] not in RESULTS:
        raise Refused(f"{label}'s result is not deployed or already deployed")
    for key, pattern in (
        ("image", dev.IMAGE), ("digest", dev.DIGEST), ("tag", dev.TAG), ("revision", dev.REVISION),
        ("run_id", dev.RUN_ID), ("run_url", dev.RUN_URL),
    ):
        if not isinstance(data[key], str) or not pattern.fullmatch(data[key]):
            raise Refused(f"{label}'s {key} is missing or malformed")
    if data["image"] != f"ghcr.io/{repository.lower()}":
        raise Refused(f"{label}'s image is not this repository's")
    if not isinstance(data["deployed_at"], str) or not DEPLOYED_AT.fullmatch(data["deployed_at"]):
        raise Refused(f"{label}'s deployed_at is malformed")
    if "requested_by" in data:
        approved = data["approved_by"]
        if not isinstance(data["requested_by"], str) or not LOGIN.fullmatch(data["requested_by"]):
            raise Refused(f"{label}'s requested_by is malformed")
        if approved != APPROVALS_UNREADABLE and not (isinstance(approved, list) and all(isinstance(x, str) and LOGIN.fullmatch(x) for x in approved)):
            raise Refused(f"{label}'s approved_by is malformed")
    return data


def read_records(repository, name, environment, keys, limit):
    """The first `limit` artifacts, newest first: each a checked dict, or the Refused that its check raised."""
    records = []
    for identifier in list_artifacts(repository, name)[:limit]:
        try:
            records.append(check_record(fetch_record(repository, identifier, name), repository, environment, keys))
        except Refused as refusal:
            records.append(refusal)
    return records


# --- resolve -------------------------------------------------------------------------------------


def choose(args):
    """(chosen record, previous prod digest or a word, recent dev records). Raises Refused before anything is deployed."""
    wanted = args.input_digest
    if wanted and not dev.DIGEST.fullmatch(wanted):
        raise Refused("the digest input is not sha256: followed by 64 lower-case hex digits")
    records = read_records(args.repository, DEV_ARTIFACT, "dev", dev.RECORD_KEYS, MAX_RECORDS)
    if not records:
        raise Refused("there is no dev deploy record to promote: let a push to main deploy to dev first")
    if not wanted:
        # The newest record decides: if it fails its check, an older one is not a safe stand-in.
        if isinstance(records[0], Refused):
            raise records[0]
        chosen = records[0]
    else:
        chosen = next((record for record in records if isinstance(record, dict) and record["digest"] == wanted), None)
        if chosen is None:
            raise Refused("no dev deploy record holds this digest: only a digest that ran in dev can go to prod")
    recent = [record for record in records[:LISTED_RECORDS] if isinstance(record, dict)]
    return chosen, previous_prod(args.repository), recent


def previous_prod(repository):
    """The digest of the newest prod record, 'none' when there is none, or 'unknown' when it cannot be read. Never raises."""
    try:
        records = read_records(repository, PROD_ARTIFACT, "prod", PROD_RECORD_KEYS, 1)
    except Refused:
        return "unknown"
    if not records:
        return "none"
    return records[0]["digest"] if isinstance(records[0], dict) else "unknown"


def write_resolve_summary(path, chosen, previous, recent, wanted):
    lines = [
        "### Promote to prod: the digest\n",
        "| | |\n|---|---|\n",
        f"| Chosen | `{chosen['digest']}` ({'named by the input' if wanted else 'dev is running it now'}) |\n",
        f"| Tag | `{chosen['tag']}` |\n",
        f"| Revision | `{chosen['revision']}` |\n",
        f"| Dev ran it | {chosen['deployed_at']}, {chosen['run_url']} |\n",
        f"| Prod runs now | {f'`{previous}`' if dev.DIGEST.fullmatch(previous) else previous} |\n",
        "\nRecent dev digests (newest first); run this workflow again with one as the `digest` input to roll back to it:\n\n",
        "| Digest | Tag | Dev deployed at |\n|---|---|---|\n",
    ]
    for record in recent:
        lines.append(f"| `{record['digest']}` | `{record['tag']}` | {record['deployed_at']} |\n")
    with open(path, "a", encoding="utf-8") as handle:
        handle.writelines(lines)


def resolve(args, out, env):
    chosen, previous, recent = choose(args)
    for key in ("image", "tag", "digest", "revision"):
        print(f"{key}={chosen[key]}", file=out)
    print(f"previous={previous}", file=out)
    print(f"to promote: {chosen['tag']} ({'named by the input' if args.input_digest else 'dev is running it now'})")
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        write_resolve_summary(summary, chosen, previous, recent, args.input_digest)
    return 0


# --- deploy --------------------------------------------------------------------------------------


def read_approvers(repository, run_id):
    """The logins that approved this run, or APPROVALS_UNREADABLE. Never raises: the deploy has already happened.

    VERIFY: GET /repos/<repo>/actions/runs/<id>/approvals lists the reviewer who approved a run that waited on an
    Environment, as a list of objects with `state` and `user.login`.
    """
    try:
        code, stdout = run_gh(["api", f"repos/{repository}/actions/runs/{run_id}/approvals"])
        if code != 0:
            return APPROVALS_UNREADABLE
        logins = []
        for item in json.loads(stdout):
            login = item["user"]["login"]
            if item.get("state") == "approved" and isinstance(login, str) and LOGIN.fullmatch(login) and login not in logins:
                logins.append(login)
        return logins
    except (Refused, ValueError, KeyError, TypeError):
        return APPROVALS_UNREADABLE


def make_record(args, result, command_id, finished, requested_by, approved_by):
    record = {
        "schema": 1, "environment": "prod", "result": result, "image": args.image, "digest": args.digest, "tag": args.tag,
        "revision": args.revision, "deployed_at": finished, "run_id": args.run_id, "run_url": args.run_url, "command_id": command_id,
        "requested_by": requested_by, "approved_by": approved_by,
    }
    assert tuple(record) == PROD_RECORD_KEYS
    return record


def write_deploy_summary(path, record, previous, line):
    approved = record["approved_by"]
    approved_text = "not readable from GitHub" if approved == APPROVALS_UNREADABLE else (", ".join(approved) or "nobody listed")
    rows = [
        ("Environment", "prod"), ("Result", record["result"]), ("Script's line", f"`{line}`"), ("Digest", f"`{record['digest']}`"),
        ("Tag", f"`{record['tag']}`"), ("Revision", f"`{record['revision']}`"),
        ("Prod ran before", f"`{previous}`" if dev.DIGEST.fullmatch(previous) else previous),
        ("Deployed at", record["deployed_at"]), ("Started by", record["requested_by"]), ("Approved by", approved_text),
        ("Command id", f"`{record['command_id']}`"), ("Health check", "200"), ("Run", record["run_url"]),
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("### Promote to prod\n\n| | |\n|---|---|\n")
        for name, value in rows:
            handle.write(f"| {name} | {value} |\n")


def deploy(args, env, clock=time.monotonic, sleep=time.sleep, now=None):
    instance, url = dev.check_inputs(args, env)
    previous = args.previous
    if not (dev.DIGEST.fullmatch(previous) or previous in ("none", "unknown")):
        raise Refused("--previous is malformed")
    actor = env.get("ACTOR", "")
    if not LOGIN.fullmatch(actor):
        raise Refused("ACTOR is missing or not a GitHub login")
    command_id = dev.send(instance, args.digest, args.revision, "prod")
    print(f"sent {dev.DOCUMENT} for {args.tag} to prod (command {command_id})")
    invocation = dev.wait(command_id, instance, clock, sleep)
    finished = (now or (lambda: datetime.datetime.now(datetime.timezone.utc)))().strftime("%Y-%m-%dT%H:%M:%SZ")
    result, _ = dev.read_result(invocation)
    line = dev.DEPLOYED if result == "deployed" else dev.ALREADY
    dev.check_health(url, sleep)
    approved_by = read_approvers(args.repository, args.run_id)
    record = make_record(args, result, command_id, finished, actor, approved_by)
    with open(args.record, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    summary = args.summary or env.get("GITHUB_STEP_SUMMARY")
    if summary:
        write_deploy_summary(summary, record, previous, line)
    print(f"prod is running {args.digest} ({result})")
    return 0


# --- command line --------------------------------------------------------------------------------


def parser():
    top = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = top.add_subparsers(dest="command", required=True)
    one = sub.add_parser("resolve")
    one.add_argument("--repository", required=True)
    one.add_argument("--input-digest", default="", help="the workflow's digest input; empty means dev's current digest")
    one.add_argument("--output", default="", help="the file the step outputs go to ($GITHUB_OUTPUT); messages go to stdout")
    two = sub.add_parser("deploy")
    for name in ("repository", "digest", "tag", "revision", "image", "run-id", "run-url", "record", "previous"):
        two.add_argument(f"--{name}", required=True)
    two.add_argument("--summary", default="")
    return top


def main(argv=None, env=None, out=None):
    args = parser().parse_args(argv)
    env = os.environ if env is None else env
    out = out or sys.stdout
    try:
        if args.command == "resolve":
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
                raise Refused("--repository is not owner/name")
            if args.output:
                with open(args.output, "a", encoding="utf-8") as handle:
                    return resolve(args, handle, env)
            return resolve(args, out, env)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            raise Refused("--repository is not owner/name")
        return deploy(args, env)
    except Refused as refusal:
        print(f"ci_promote: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
