"""The on-call agent's guard (#298). Standard library only; the publish job runs the copy on `main`.

    python guard.py check-patch fix.patch --ref oncall/<run id> --run-id <run id>
    python guard.py check-pr --repo OWNER/REPO --number N

`check-patch` reads `fix.patch` as a unified git diff and exits 1, before any push, when:
  - a path is a deny-listed one (DENY_DIRS, DENY_NAMES: the list in MANDATE.md), also by case,
    a leading `./` or a rename or copy into it, or a path outside the repository (absolute, `..`);
  - more than MAX_LINES lines change (added plus deleted) or more than MAX_FILES files;
  - no test file gains a line (TEST_FILES below);
  - a mode is not 100644 (executable 100755, symlink 120000, anything else);
  - the patch is binary, or cannot be parsed, so its lines cannot be counted;
  - the ref is anything but `oncall/<run id>` (--run-id is required, so the run's own id is always checked).
Exit codes: 0 allowed, 1 refused (one fixed line per reason on stderr), 2 a usage or setup error.

`check-pr` fetches the opened pull request and exits 1, after closing it, when it is not a draft or
auto-merge is not off (or the answer cannot be read). It needs GITHUB_TOKEN with pull-requests: write.

Test files are identified by the `TEST_FILES` patterns of `agent-kit.conf` (shell patterns where `*`
also matches `/`), copied below because this script cannot read the file at publish time;
tests/test_oncall_guard.py fails when the copy differs from the file.
"""

import argparse
import fnmatch
import http.client
import json
import os
import re
import sys

MAX_LINES = 200
MAX_FILES = 5
MAX_PATCH_BYTES = 1024 * 1024

# The mandate's list (MANDATE.md; a test keeps them equal). Matched on the lower-cased path.
# `.git` is added: a patch cannot plant a hook there. A name is denied in any folder, because
# pytest reads a pyproject.toml in a folder above the file it runs.
DENY_DIRS = (".github", ".claude", ".kiro", ".githooks", ".git", "terraform", "observability/oncall")
DENY_NAMES = ("makefile", "agents.md", "pyproject.toml", "uv.lock")

# Copy of TEST_FILES in agent-kit.conf.
TEST_FILES = ("tests/*", "*test_*.py", "*_test.py", "*conftest.py", "*.test.*", "*.spec.*", "e2e/*", "frontend/src/test/*", "*vitest.config.*")

REF = re.compile(r"oncall/[0-9]{1,20}")
RUN_ID = re.compile(r"[0-9]{1,20}")
PATH_TEXT = re.compile(r"[\x21-\x7e]+")  # no space, control or non-ASCII character
MODE = re.compile(r"[0-7]{6}")
# Text after the closing `@@` is git's function context; it is never counted, so any text is accepted.
HUNK = re.compile(r"@@ -[0-9]+(?:,([0-9]+))? \+[0-9]+(?:,([0-9]+))? @@")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class Unparsable(Exception):
    """The patch is not one this guard can count."""


def _show(text: str) -> str:
    return ascii(text)[:80]


def check_ref(ref: str, run_id: str | None = None) -> list[str]:
    """Reasons to refuse the target ref; empty when it is `oncall/<run id>`."""
    if not isinstance(ref, str) or not REF.fullmatch(ref):
        return [f"the ref {_show(str(ref))} is not oncall/<numeric run id>"]
    if run_id is not None and ref != f"oncall/{run_id}":
        return [f"the ref {_show(ref)} is not oncall/{_show(run_id)}"]
    return []


def normal(path: str) -> str:
    """The repository-relative path with leading `./` removed, or Unparsable when it is not inside the repository."""
    if not PATH_TEXT.fullmatch(path) or "\\" in path or '"' in path:
        raise Unparsable(f"the path {_show(path)} has a character that is not allowed")
    parts = path.split("/")
    while parts and parts[0] == ".":
        parts.pop(0)
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise Unparsable(f"the path {_show(path)} is empty, absolute or leaves the repository")
    return "/".join(parts)


def denied(path: str) -> bool:
    """True when the normalised path is deny-listed, whatever its case."""
    low = path.casefold()
    return low.rsplit("/", 1)[-1] in DENY_NAMES or any(low == d or low.startswith(d + "/") for d in DENY_DIRS)


def is_test_file(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in TEST_FILES)


def _git_path(token: str, prefix: str) -> str:
    if not token.startswith(prefix):
        raise Unparsable(f"a path in a header has no {prefix} prefix")
    return normal(token[len(prefix):])


def _mode_reason(mode: str) -> str | None:
    if not MODE.fullmatch(mode):
        raise Unparsable(f"a mode line is not a mode: {_show(mode)}")
    if mode == "100644":
        return None
    return "a symlink mode (120000)" if mode == "120000" else f"a mode other than 100644 ({mode}, executable or special)"


def parse(text: str) -> list[dict]:
    """One record per file: old and new path, added and deleted line counts, deleted flag, mode reasons."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("diff --git "):
            raise Unparsable("a line is outside any file section")
        tokens = line.split(" ")
        if len(tokens) != 4:
            raise Unparsable("a diff --git line does not hold exactly two paths")
        rec = {"olds": {_git_path(tokens[2], "a/")}, "news": {_git_path(tokens[3], "b/")}, "added": 0, "deleted": 0,
               "gone": False, "renamed": False, "modes": []}
        files.append(rec)
        i += 1
        while i < len(lines) and not lines[i].startswith("diff --git "):
            line = lines[i]
            if line.startswith("@@"):
                i = _hunk(lines, i, rec)
                continue
            rest = line.partition(" ")[2]
            if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
                raise Unparsable("the patch is binary, so its lines cannot be counted")
            if line.startswith(("old mode ", "new mode ", "new file mode ", "deleted file mode ")):
                rec["modes"].append(line.rsplit(" ", 1)[1])
                rec["gone"] |= line.startswith("deleted")
            elif line.startswith("index "):
                parts = line.split(" ")
                if len(parts) == 3:
                    rec["modes"].append(parts[2])
            elif line.startswith(("rename from ", "copy from ")):
                rec["renamed"] = True
                rec["olds"].add(normal(line.split(" ", 2)[2]))
            elif line.startswith(("rename to ", "copy to ")):
                rec["renamed"] = True
                rec["news"].add(normal(line.split(" ", 2)[2]))
            elif line.startswith("--- "):
                if rest != "/dev/null":
                    rec["olds"].add(_git_path(rest, "a/"))
            elif line.startswith("+++ "):
                if rest == "/dev/null":
                    rec["gone"] = True
                else:
                    rec["news"].add(_git_path(rest, "b/"))
            elif not line.startswith(("similarity index ", "dissimilarity index ")):
                raise Unparsable("a header line is not one this guard knows")
            i += 1
        if len(rec["olds"]) != 1 or len(rec["news"]) != 1 or (not rec["renamed"] and rec["olds"] != rec["news"]):
            raise Unparsable("the paths in one file section disagree")
    return files


def _hunk(lines: list[str], i: int, rec: dict) -> int:
    """Consume one hunk by its line counts (so a content line `--- x` is not a header); return the next index."""
    match = HUNK.match(lines[i])
    if not match:
        raise Unparsable("a hunk header is malformed")
    old, new = (1 if n is None else int(n) for n in match.groups())
    i += 1
    while old > 0 or new > 0:
        if i >= len(lines):
            raise Unparsable("a hunk is shorter than its header says")
        sign = lines[i][:1]
        if sign == "\\":  # "\ No newline at end of file": not a line of either side
            i += 1
            continue
        if sign == "-" and old > 0:
            old -= 1
            rec["deleted"] += 1
        elif sign == "+" and new > 0:
            new -= 1
            rec["added"] += 1
        elif sign in (" ", "") and old > 0 and new > 0:
            old -= 1
            new -= 1
        else:
            raise Unparsable("a hunk line does not fit its header's counts")
        i += 1
    while i < len(lines) and lines[i].startswith("\\"):  # "\ No newline at end of file"
        i += 1
    return i


def check_patch(text: str, ref: str, run_id: str | None = None) -> list[str]:
    """Every reason to refuse; empty means allowed. Nothing is written or run."""
    reasons = check_ref(ref, run_id)
    try:
        files = parse(text)
    except Unparsable as error:
        return reasons + [str(error)]
    if len(files) > MAX_FILES:
        reasons.append(f"the patch changes {len(files)} files; the limit is {MAX_FILES}")
    changed = sum(f["added"] + f["deleted"] for f in files)
    if changed > MAX_LINES:
        reasons.append(f"the patch changes {changed} lines; the limit is {MAX_LINES}")
    for f in files:
        for path in sorted(f["olds"] | f["news"]):
            if denied(path):
                reasons.append(f"the patch touches the deny-listed path {_show(path)}")
        for mode in f["modes"]:
            reason = _mode_reason(mode)
            if reason:
                reasons.append(f"{_show(sorted(f['news'])[0])} has {reason}")
    # A test file counts when it is not deleted and gains a line.
    if not any(is_test_file(next(iter(f["news"]))) and not f["gone"] and f["added"] > 0 for f in files):
        reasons.append("the patch adds lines to no test file")
    return reasons


def check_pull_request(answer, close) -> list[str]:
    """Reasons the opened pull request is refused (GET /pulls/<n>'s answer). Calls `close()` once when there are any."""
    reasons = []
    if not isinstance(answer, dict):
        reasons.append("the pull request answer cannot be read")
    else:
        if answer.get("draft") is not True:
            reasons.append("the pull request is not a draft")
        if "auto_merge" not in answer or answer["auto_merge"] is not None:
            reasons.append("auto-merge is not off")
    if reasons:
        close()
    return reasons


def _api(method: str, path: str, token: str, body: dict | None = None):
    connection = http.client.HTTPSConnection("api.github.com", timeout=10)
    try:
        headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "socboard-oncall-guard"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read(1024 * 1024)
        return response.status, json.loads(data)
    finally:
        connection.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("check-patch")
    one.add_argument("patch")
    one.add_argument("--ref", required=True)
    one.add_argument("--run-id", required=True)
    two = sub.add_parser("check-pr")
    two.add_argument("--repo", required=True)
    two.add_argument("--number", required=True)
    args = parser.parse_args(argv)
    if args.command == "check-patch":
        if args.run_id is not None and not RUN_ID.fullmatch(args.run_id):
            print("refused: --run-id must be digits", file=sys.stderr)
            return 2
        try:
            with open(args.patch, "rb") as handle:
                raw = handle.read(MAX_PATCH_BYTES + 1)
            if len(raw) > MAX_PATCH_BYTES:
                raise ValueError
            text = raw.decode("utf-8")
        except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
            print("refused: the patch cannot be read as text or is over 1 MiB", file=sys.stderr)
            return 1
        reasons = check_patch(text, args.ref, args.run_id)
        print("test files: the TEST_FILES patterns of agent-kit.conf (copied in guard.py)")
        for reason in reasons:
            print("refused: " + reason, file=sys.stderr)
        return 1 if reasons else 0
    token = os.environ.get("GITHUB_TOKEN", "")
    if not (token and REPO.fullmatch(args.repo) and RUN_ID.fullmatch(args.number)):
        print("refused: needs GITHUB_TOKEN, --repo OWNER/REPO and a numeric --number", file=sys.stderr)
        return 2
    path = f"/repos/{args.repo}/pulls/{args.number}"

    def close():
        try:
            _api("PATCH", path, token, {"state": "closed"})
        except (OSError, http.client.HTTPException, ValueError):
            print("could not close the pull request: close it by hand", file=sys.stderr)

    try:
        status, answer = _api("GET", path, token)
        answer = answer if status == 200 else None
    except (OSError, http.client.HTTPException, ValueError):
        answer = None
    reasons = check_pull_request(answer, close)
    for reason in reasons:
        print("refused: " + reason + "; the pull request was closed", file=sys.stderr)
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
