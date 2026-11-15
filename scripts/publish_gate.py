"""The decisions of the image publishing workflow (#235), kept here so they can be tested.

    python3 scripts/publish_gate.py gate --repository OWNER/REPO --sha SHA --event push --branch main \\
        --head-repository OWNER/REPO --runs runs.json
    python3 scripts/publish_gate.py tag --sha SHA --timestamp 2026-11-15T09:12:00+10:00
    python3 scripts/publish_gate.py name --repository OWNER/REPO

`gate` reads the workflow runs of one commit (the `workflow_runs` objects of GitHub's API, as a JSON list)
and prints `ready=true` or `ready=false`, a `reason=` line and the id of the Compose run that holds the
tested image, in the format of `$GITHUB_OUTPUT`. It is ready only when the event is a push to `main` of
this repository, every workflow in REQUIRED has a run, and every run of the commit (the required ones and
any other, such as the Terraform check when the commit touched it) is completed with the conclusion
`success`. A run that has not completed is "not yet": a later completion triggers the workflow again.
Anything else is a refusal with its reason. Always exit 0 for a decision; exit 2 for bad input.

`tag` prints `YYYYMMDD-HHMMSS-<7 characters of the sha>`, in UTC, from the commit's own time, so a re-run
of the same commit gets the same tag. There is no `latest`. `name` prints `ghcr.io/<repository, lower case>`.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

PUBLISH_WORKFLOW = "socboard publish"
COMPOSE_WORKFLOW = "socboard compose"
REQUIRED = ("socboard verify", "socboard postgres", "socboard image", COMPOSE_WORKFLOW)
SHA = re.compile(r"[0-9a-f]{40}")
# An owner is letters, digits and hyphens; a name is letters, digits, `_`, `.` and `-`, and is not `.` or `..`.
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/(?!\.{1,2}$)[A-Za-z0-9_.-]+")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


class BadInput(ValueError):
    """The input is not usable. The message is fixed text."""


def decide(*, repository, sha, event, branch, head_repository, runs):
    """(ready, reason, compose_run_id) for one commit. `runs` are workflow runs of any commit."""
    if event != "push" or branch != "main" or head_repository != repository:
        return False, "not a push to main of this repository", None
    mine = [run for run in runs if run.get("head_sha") == sha and run.get("event") == "push" and run.get("name") != PUBLISH_WORKFLOW]
    latest = {}
    for run in mine:  # a re-run keeps its id; of two runs of one workflow the newer has the larger id
        run_id = int(run["id"])  # a run without an id is unusable input, not a run to skip
        if run["name"] not in latest or run_id > int(latest[run["name"]]["id"]):
            latest[run["name"]] = run
    missing = [name for name in REQUIRED if name not in latest]
    if missing:
        return False, f"not yet: no run of {', '.join(missing)}", None
    unfinished = sorted(name for name, run in latest.items() if run.get("status") != "completed")
    if unfinished:
        return False, f"not yet: {', '.join(unfinished)} still running", None
    unsuccessful = sorted(f"{name} ({run.get('conclusion')})" for name, run in latest.items() if run.get("conclusion") != "success")
    if unsuccessful:
        return False, f"refused: {', '.join(unsuccessful)} did not succeed", None
    return True, "every check of the commit succeeded", latest[COMPOSE_WORKFLOW]["id"]


def tag_for(sha, timestamp):
    if not SHA.fullmatch(sha):
        raise BadInput("sha must be 40 lower-case hex characters")
    if not TIMESTAMP.fullmatch(timestamp):
        raise BadInput("timestamp must be an ISO 8601 date and time with an offset")
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise BadInput("timestamp must carry an offset")
    return f"{moment.astimezone(timezone.utc):%Y%m%d-%H%M%S}-{sha[:7]}"


def image_name(repository):
    if not REPOSITORY.fullmatch(repository):
        raise BadInput("repository must be OWNER/REPO")
    return f"ghcr.io/{repository.lower()}"


def main(argv):
    parser = argparse.ArgumentParser(prog="publish_gate.py")
    commands = parser.add_subparsers(dest="command", required=True)
    gate = commands.add_parser("gate")
    for name in ("repository", "sha", "event", "branch", "head-repository", "runs"):
        gate.add_argument(f"--{name}", required=True)
    tag = commands.add_parser("tag")
    tag.add_argument("--sha", required=True)
    tag.add_argument("--timestamp", required=True)
    name = commands.add_parser("name")
    name.add_argument("--repository", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    try:
        if args.command == "gate":
            if not SHA.fullmatch(args.sha):
                raise BadInput("sha must be 40 lower-case hex characters")
            with open(args.runs, encoding="utf-8") as handle:
                runs = json.load(handle)
            if not isinstance(runs, list) or not all(isinstance(run, dict) for run in runs):
                raise BadInput("runs must be a JSON list of workflow runs")
            ready, reason, compose_run = decide(
                repository=args.repository,
                sha=args.sha,
                event=args.event,
                branch=args.branch,
                head_repository=args.head_repository,
                runs=runs,
            )
            print(f"ready={str(ready).lower()}")
            print(f"reason={reason}")
            print(f"compose_run_id={compose_run if compose_run is not None else ''}")
        elif args.command == "tag":
            print(tag_for(args.sha, args.timestamp))
        else:
            print(image_name(args.repository))
    except (BadInput, OSError, ValueError, KeyError) as error:
        print(f"publish_gate: {error if isinstance(error, BadInput) else 'unusable input'}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
