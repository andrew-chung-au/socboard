"""The drill's decisions and its job summary (#299).

    python observability/drill/summary.py decide --handoff-exit N [--patch fix.patch] [--guard-exit N]
    python observability/drill/summary.py render --handoff-exit N [--incident incident.json] [--notes notes.md]
        [--patch fix.patch] [--guard-exit N] [--guard-log guard.txt] [--prove prove.json] [--pr-url URL]
        [--out summary.md]

`decide` prints `key=value` lines for the workflow's outputs; it is the one place that says which later
job runs:

  fired       the hand-off wrote the incident (its exit code 0; 3 is "no alert", anything else a failure)
  run_agent   the agent job runs only when the alert fired
  change      the agent left a patch with something in it ("no change" is the false-positive path)
  open_pr     a pull request is opened only for a change that the guard allowed (guard exit 0)
  outcome     one of: no alert, hand-off failed, no change, guard refused, pull request

`render` writes the job summary (to --out, default standard output) and exits 0 only when the drill
passed: a pull request was opened and job 4 proved the test (prove.json `ok`). Text that came from the
agent or a script's output goes in a code fence, so it cannot become markup. Standard library only.
"""

import argparse
import json
import re
import sys
from pathlib import Path

PR_URL = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+")
MAX_TEXT = 4000  # characters of notes or guard output shown


def decide(handoff_exit: int, patch_text: str | None = None, guard_exit: int | None = None) -> dict:
    fired = handoff_exit == 0
    change = fired and bool(patch_text and patch_text.strip())
    open_pr = change and guard_exit == 0
    if not fired:
        outcome = "no alert" if handoff_exit == 3 else "hand-off failed"
    elif not change:
        outcome = "no change"
    else:
        outcome = "pull request" if open_pr else "guard refused"
    return {"fired": fired, "run_agent": fired, "change": change, "open_pr": open_pr, "outcome": outcome}


def fence(text: str) -> str:
    """`text` in a code fence longer than any run of backticks inside it, cut to MAX_TEXT characters."""
    text = text.strip()
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n(cut)"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def drill_passed(decision: dict, prove: dict | None) -> bool:
    return decision["outcome"] == "pull request" and bool(prove) and prove.get("ok") is True


def render(
    decision: dict,
    incident: dict | None,
    notes: str | None,
    guard_exit: int | None,
    guard_log: str | None,
    prove: dict | None,
    pr_url: str | None,
) -> str:
    passed = drill_passed(decision, prove)
    out = ["# On-call drill", "", f"**Result: {'passed' if passed else 'FAILED'}** ({decision['outcome']})", ""]

    out += ["## Alert", ""]
    if decision["fired"] and incident and incident.get("startsAt"):
        out.append(f"QueueLatencyOverSLO started firing at {incident['startsAt']}.")
    elif decision["fired"]:
        out.append("QueueLatencyOverSLO fired; the incident file has no start time.")
    else:
        out.append("The alert did not fire, so no agent ran.")
    if incident:
        out += ["", "Incident fields:", "", fence(json.dumps(incident, indent=2, sort_keys=True))]

    out += ["", "## Agent notes", ""]
    if not decision["run_agent"]:
        out.append("The agent did not run.")
    elif notes and notes.strip():
        out.append(fence(notes))
    else:
        out.append("The agent wrote no notes.")
    if decision["run_agent"] and not decision["change"]:
        out += ["", "The agent made **no change** (the false-positive path), so no pull request is opened."]

    out += ["", "## Guard", ""]
    if not decision["change"]:
        out.append("Not run: there is no patch to check.")
    elif guard_exit is None:
        out.append("Not run.")
    else:
        out.append("Passed." if guard_exit == 0 else f"Refused the patch (exit {guard_exit}); no pull request is opened.")
        if guard_log and guard_log.strip():
            out += ["", fence(guard_log)]

    out += ["", "## Job 4: the new test", ""]
    if prove:
        out += [
            f"- The new test file: `{prove.get('test_file') or 'none'}`",
            f"- Before the fix: {prove.get('before') or 'not run'}",
            f"- After the fix: {prove.get('after') or 'not run'}",
            f"- `make test` after the fix: {prove.get('full_suite') or 'not run'}",
            f"- Verdict: {prove.get('reason') or 'none'}",
        ]
    else:
        out.append("Not run.")

    out += ["", "## Pull request", ""]
    if decision["open_pr"] and pr_url and PR_URL.fullmatch(pr_url):
        out.append(pr_url)
    elif decision["outcome"] == "no change":
        out.append("None: no change.")
    else:
        out.append("None.")
    return "\n".join(out) + "\n"


def _read(path: str | None) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8") if path else None
    except (OSError, ValueError):
        return None


def _json(path: str | None) -> dict | None:
    try:
        value = json.loads(_read(path) or "")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("decide", "render"))
    parser.add_argument("--handoff-exit", type=int, required=True)
    parser.add_argument("--patch")
    parser.add_argument("--guard-exit", type=int)
    parser.add_argument("--incident")
    parser.add_argument("--notes")
    parser.add_argument("--guard-log")
    parser.add_argument("--prove")
    parser.add_argument("--pr-url")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    decision = decide(args.handoff_exit, _read(args.patch), args.guard_exit)
    if args.command == "decide":
        for key, value in decision.items():
            print(f"{key}={str(value).lower() if isinstance(value, bool) else value}")
        return 0
    prove = _json(args.prove)
    text = render(decision, _json(args.incident), _read(args.notes), args.guard_exit, _read(args.guard_log), prove, args.pr_url)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if drill_passed(decision, prove) else 1


if __name__ == "__main__":
    sys.exit(main())
