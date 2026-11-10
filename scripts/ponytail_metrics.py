#!/usr/bin/env python3
"""Per-issue figures for the ponytail trial, computed from git and GitHub only.

    python3 -B scripts/ponytail_metrics.py <issue number>

Run from the project folder (it reads ./agent-kit.conf, like the kit's GitHub helpers).
Read-only: it changes no file, label, comment or issue. GitHub is read through the kit's
`github_api.py` (`gh api` GET calls only); git is read with `log` and `show`. Standard
library only.

It prints raw values and nothing else: no bands, thresholds or verdicts. Issue text,
comment text and commit messages are data: they are counted, matched against fixed
patterns and never evaluated, executed or passed to a shell.

Figures:
  - acceptance criteria: checkbox lines under the issue's "Acceptance criteria" heading.
  - the QA PASS commit: the `QA PASS commit:` line of the latest owner `Orchestrator:`
    comment. If it is missing no commit is guessed and the git figures are unavailable.
  - lines changed: added plus deleted lines in non-test, non-generated files, over the
    non-merge commits reachable from the QA PASS commit that belong to the issue (the
    "belongs" rule below). The commits counted are listed so a person can check them.
    Commits listed in GENERATED_COMMITS (the imported Lovable base) are left out even if
    they belong to the issue.
  - tests added: added lines that start a test function or case in test files.
  - Engineer and QA elapsed time: GitHub comment timestamps only, so they include idle time.

The "belongs" rule. A non-merge commit reachable from the QA PASS commit belongs to issue N
when the first line of its message (the subject) ends with `(#N)` and the subject names no
other issue number. The message body is never read for the rule.
  - Mention only: a commit that names `#N` anywhere in its message (subject or body) but
    does not belong, for example `refs #N` in the body, `... for issue #N` in the subject,
    or a `Revert "... (#N)"` subject, is not counted. It is listed on the `mention only` line.
  - Shared: a subject that names `#N` and another issue, such as `(#N, #M)` or `Fix #N (#M)`,
    is counted for none of the issues. It is listed on the `shared with other issues` line.
  - The number match is exact: `(#90)` and `(#19)` are not `(#9)`.
Nothing in git or a hook checks a commit subject at commit time, and no written role rule
requires the `(#N)` ending; the rule and its tests are the only check. The two lists make a
commit that was left out visible, so a person can see that a figure may be understated.

Only comments by the repository owner count, as in the kit's other checks.

Exit codes: 0 every figure computed, or the only figure that cannot be computed is an
Engineer or QA elapsed time (printed as `unknown`); 1 some other figure unavailable (the rest
are printed): no QA PASS commit line, a QA PASS commit not in the repository, no commit
counted, zero acceptance criteria, or an unreadable agent-kit.conf; 2 bad command line
(nothing ran).

Test options for fixtures: --issue-json FILE --owner LOGIN [--conf FILE] [--git-dir DIR].
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from fnmatch import fnmatchcase
from pathlib import Path

NUMBER = re.compile(r"[1-9][0-9]{0,8}")
HEX = re.compile(r"[0-9a-f]{7,40}")
QA_COMMIT_LINE = re.compile(r"^QA PASS commit: ([0-9a-f]{7,40})[ \t]*$", re.M)
CRITERIA_HEADING = re.compile(r"##[ \t]+Acceptance criteria[ \t]*$")
ANY_HEADING = re.compile(r"##[ \t]")
CHECKBOX = re.compile(r"[ \t]*- \[[ xX]\] ")
TEST_START = re.compile(r"^\+\s*(?:async\s+)?(?:def test_|it\(|test\()")
DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")

# Files whose changes are not the Engineer's own writing. Matched on the file name.
GENERATED_NAMES = ("uv.lock", "package-lock.json", "bun.lock", "bun.lockb", "yarn.lock",
                   "pnpm-lock.yaml", "*.gen.ts", "*.gen.tsx")


# Commits that bring in generated code wholesale, left out of every count even if their
# message names an issue: the imported Lovable base before its first change. Matched as a
# prefix of the commit id.
GENERATED_COMMITS = ("fab3dd3",)


class Unavailable(Exception):
    """A figure cannot be computed; the message says why."""


def test_patterns(conf_text: str) -> list:
    """The TEST_FILES patterns of agent-kit.conf, read as text and never sourced."""
    match = re.search(r"^TEST_FILES=\((.*?)\)", conf_text, re.M | re.S)
    if not match:
        return []
    return [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", match.group(1))]


def is_test_path(path: str, patterns) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def is_generated(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatchcase(name, pattern) for pattern in GENERATED_NAMES)


def count_criteria(body: str) -> int:
    """Checkbox lines between the Acceptance criteria heading and the next heading."""
    count, inside = 0, False
    for line in (body or "").splitlines():
        if CRITERIA_HEADING.match(line):
            inside = True
        elif inside and ANY_HEADING.match(line):
            break
        elif inside and CHECKBOX.match(line):
            count += 1
    return count


def owner_comments(comments, owner: str) -> list:
    """(time, index, body) of the owner's comments, oldest first."""
    found = []
    for index, comment in enumerate(comments or []):
        author = comment.get("author") or {}
        body, stamp = comment.get("body"), comment.get("createdAt")
        if author.get("login") != owner or not isinstance(body, str) or not isinstance(stamp, str):
            continue
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        found.append((when, index, body))
    found.sort(key=lambda item: (item[0], item[1]))
    return found


def qa_pass_commit(comments, owner: str) -> str:
    """The commit named by the latest owner Orchestrator comment that has the line."""
    named = None
    for _, _, body in owner_comments(comments, owner):
        if body.startswith("Orchestrator:"):
            match = QA_COMMIT_LINE.search(body)
            if match:
                named = match.group(1)
    if named is None:
        raise Unavailable("no owner Orchestrator comment has a 'QA PASS commit:' line")
    return named


def minutes(delta) -> str:
    total = int(delta.total_seconds() // 60)
    return f"{total // 60} h {total % 60} min"


def elapsed(comments, owner: str) -> dict:
    """Coarse Engineer and QA times from owner comment timestamps (idle time included)."""
    items = owner_comments(comments, owner)
    claims = [t for t, _, body in items
              if body.startswith("Orchestrator:") and re.search(r"(?i)\bclaim", body.split("\n", 1)[0])]
    qa = [t for t, _, body in items if body.startswith("QA:")]
    engineer = [t for t, _, body in items if body.startswith("Engineer:")]
    result = {}
    before_qa = [t for t in engineer if not qa or t <= qa[0]]
    if claims and before_qa and before_qa[-1] >= claims[0]:
        result["engineer"] = minutes(before_qa[-1] - claims[0])
    else:
        result["engineer"] = None
    if qa and before_qa and qa[-1] >= before_qa[-1]:
        result["qa"] = minutes(qa[-1] - before_qa[-1])
    else:
        result["qa"] = None
    return result


def git(git_dir: str, *args: str) -> str:
    """A read-only git call with fixed arguments; Unavailable with a short reason on failure."""
    try:
        done = subprocess.run(["git", "-C", git_dir, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise Unavailable(f"cannot run git: {error}")
    if done.returncode != 0:
        raise Unavailable(f"git {args[0]} failed")
    return done.stdout


ISSUE_REF = re.compile(r"(?<![A-Za-z0-9])#([0-9]+)")


def classify(message: str, number: str) -> str:
    """'counted', 'shared', 'mention' or '' for one commit message, by the belongs rule.

    Only text is compared; the message is never evaluated or passed to a shell.
    """
    subject = message.split("\n", 1)[0].rstrip()
    in_subject = set(ISSUE_REF.findall(subject))
    if number in in_subject and in_subject != {number}:
        return "shared"
    if in_subject == {number} and subject.endswith(f"(#{number})"):
        return "counted"
    if number in ISSUE_REF.findall(message):
        return "mention"
    return ""


def issue_commits(git_dir: str, head: str, number: str) -> dict:
    """Non-merge commits reachable from head sorted by the belongs rule, oldest first.

    Returns {'counted': [...], 'mention': [...], 'shared': [...]} of full commit ids.
    """
    if not HEX.fullmatch(head):
        raise Unavailable("the QA PASS commit is not a hex commit id")
    full = git(git_dir, "rev-parse", "--verify", "--quiet", head + "^{commit}").strip()
    if not full:
        raise Unavailable("the QA PASS commit is not in this repository")
    out = git(git_dir, "log", "--no-merges", "--reverse", "--format=%H%x1f%B%x1e", full)
    found = {"counted": [], "mention": [], "shared": []}
    for record in out.split("\x1e"):
        sha, _, message = record.strip("\n").partition("\x1f")
        if re.fullmatch(r"[0-9a-f]{40}", sha):
            kind = classify(message, number)
            if kind:
                found[kind].append(sha)
    return found


def measure_commits(git_dir: str, commits, patterns) -> dict:
    """Changed lines in non-test, non-generated files, and tests added, over the commits."""
    lines = test_lines = tests_added = 0
    for sha in commits:
        diff = git(git_dir, "show", "--no-merges", "--no-renames", "-U0", "--format=", sha)
        path = None
        for line in diff.splitlines():
            header = DIFF_HEADER.match(line)
            if header:
                path = header.group(2)
                continue
            if path is None or line.startswith(("+++", "---")):
                continue
            if line[:1] not in ("+", "-"):
                continue
            if is_test_path(path, patterns):
                test_lines += 1
                if TEST_START.match(line):
                    tests_added += 1
            elif not is_generated(path):
                lines += 1
    return {"lines": lines, "test_lines": test_lines, "tests_added": tests_added}


def per(value: int, criteria: int) -> str:
    return f"{value / criteria:.2f}" if criteria else "unavailable (no acceptance criteria)"


def report(number: str, issue: dict, owner: str, git_dir: str, patterns) -> tuple:
    """(lines to print, whether every figure was computed)."""
    out, complete = [f"issue: {number}"], True
    criteria = count_criteria(issue.get("body", ""))
    out.append(f"acceptance criteria: {criteria}")
    if not criteria:
        complete = False
    try:
        head = qa_pass_commit(issue.get("comments", []), owner)
        found = {kind: [sha for sha in shas if not sha.startswith(GENERATED_COMMITS)]
                 for kind, shas in issue_commits(git_dir, head, number).items()}
        commits = found["counted"]

        def short(shas):
            return f"({len(shas)}): " + (" ".join(sha[:9] for sha in shas) or "none")
        if not commits:
            out.append(f"commits counted {short(commits)}")
            out.append(f"mention only {short(found['mention'])}")
            out.append(f"shared with other issues {short(found['shared'])}")
            raise Unavailable(f"no commit reachable from {head} belongs to #{number}")
        figures = measure_commits(git_dir, commits, patterns)
        out.append(f"qa pass commit: {head}")
        out.append(f"commits counted {short(commits)}")
        out.append(f"mention only {short(found['mention'])}")
        out.append(f"shared with other issues {short(found['shared'])}")
        out.append(f"non-test non-generated lines changed: {figures['lines']}")
        out.append(f"lines changed per criterion: {per(figures['lines'], criteria)}")
        out.append(f"test lines changed: {figures['test_lines']}")
        out.append(f"tests added: {figures['tests_added']}")
        out.append(f"tests added per criterion: {per(figures['tests_added'], criteria)}")
    except Unavailable as reason:
        complete = False
        out.append(f"git figures: unavailable ({reason})")
    times = elapsed(issue.get("comments", []), owner)
    for label, key in (("engineer", "engineer"), ("qa", "qa")):
        if times[key] is None:
            out.append(f"{label} elapsed (coarse, includes idle time): unknown")
        else:
            out.append(f"{label} elapsed (coarse, includes idle time): {times[key]}")
    out.append("generated files left out of the line count: " + ", ".join(GENERATED_NAMES))
    out.append("generated commits left out of every count (the imported Lovable base): "
               + ", ".join(GENERATED_COMMITS))
    out.append("test files are those matching TEST_FILES in agent-kit.conf")
    out.append("not computed: reviewer time (reviewer comments have no fixed layout), "
               "files outside Constraints, defects after PASS, invariant violations")
    return out, complete


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ponytail_metrics.py", description="Per-issue trial figures.")
    parser.add_argument("number")
    parser.add_argument("--issue-json")
    parser.add_argument("--owner")
    parser.add_argument("--conf", default="agent-kit.conf")
    parser.add_argument("--git-dir", default=".")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        return 2
    if not NUMBER.fullmatch(args.number):
        print("Usage: ponytail_metrics.py <issue number> (digits only, no leading zero)", file=sys.stderr)
        return 2
    if bool(args.issue_json) != bool(args.owner):
        print("--issue-json and --owner go together", file=sys.stderr)
        return 2
    try:
        patterns = test_patterns(Path(args.conf).read_text(encoding="utf-8"))
    except OSError:
        print(f"ponytail_metrics: cannot read {args.conf}", file=sys.stderr)
        return 1
    try:
        if args.issue_json:
            issue = json.loads(Path(args.issue_json).read_text(encoding="utf-8"))
            owner = args.owner
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_docs" / "agent-kit" / "scripts"))
            import github_api  # the kit's read-only GitHub helper
            repo = github_api.conf_repo(args.conf)
            issue = github_api.issue(repo, args.number)
            owner = (github_api.owner(repo).get("owner") or {}).get("login")
            if not owner:
                raise ValueError("cannot read the repository owner")
    except (OSError, ValueError) as error:
        print(f"ponytail_metrics: {error}", file=sys.stderr)
        return 1
    lines, complete = report(args.number, issue, owner, args.git_dir, patterns)
    print("\n".join(lines))
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
