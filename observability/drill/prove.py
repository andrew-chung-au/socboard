"""Job 4 of the drill (#299): the agent's new test fails before its fix and passes after it.

    python observability/drill/prove.py fix.patch --result prove.json

Run in a checkout of the drill branch (defect applied, nothing else changed) after `make install`.
`fix.patch` must add lines to exactly one test file under tests/. The steps, in order:

  1. apply only that test file from the patch, run `make test-one FILE=<it>`: it must FAIL on an
     assertion (pytest's "N failed"; a collection error or "no tests ran" is not a failure of the defect);
  2. apply the rest of the patch, run the test again: it must pass;
  3. run `make test`: it must pass.

Anything else marks the drill failed (exit 1), and a test that passes before the fix stops the run at
step 1. `make` reports every pytest failure as its own exit 2, so the outcome is read from pytest's
summary line. The commands' output goes to the job log, which is the pull request's test evidence
(a pull request opened with GITHUB_TOKEN starts no CI, #287). Exit codes: 0 proved, 1 not proved,
2 an unreadable patch. Standard library only.
"""

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# A test module under tests/, no `..` and no glob or make character, so the name is safe in a
# `git apply --include=` and a `make FILE=`.
TEST_PATH = re.compile(r"tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_-]+\.py")
FAILED = re.compile(r"\b[0-9]+ failed\b")
TIMEOUT = 1800  # seconds per command

Run = Callable[[list[str]], tuple[int, str]]  # argv -> (exit code, output)


def named_tests(patch_text: str) -> list[str]:
    """Test modules under tests/ that gain a line in the patch.

    shortcut: an added line that itself reads `++ b/...` could pass for a header; the guard (#298) is the
    boundary, this only picks the file to run. Upgrade when the drill needs a stricter parse.
    """
    found: list[str] = []
    current = None
    for line in patch_text.split("\n"):
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("+++ b/"):
            current = line[6:]
        elif current and line.startswith("+") and TEST_PATH.fullmatch(current) and current not in found:
            found.append(current)
    return found


def classify(code: int, output: str) -> str:
    """`passes`, `fails` (an assertion failed) or `error` (anything else: a collection error, no tests)."""
    if code == 0:
        return "passes"
    return "fails" if FAILED.search(output) else "error"


def prove(patch_text: str, patch_path: str, run: Run, say: Callable[[str], None]) -> dict:
    """The proof. `run` runs a command in the checkout. The result's `ok` is True only when every step held."""
    result = {"ok": False, "test_file": None, "before": None, "after": None, "full_suite": None, "reason": ""}
    files = named_tests(patch_text)
    if len(files) != 1:
        result["reason"] = f"the patch must add lines to exactly one test file under tests/ (it adds to {len(files)})"
        return result
    name = result["test_file"] = files[0]

    code, _ = run(["git", "apply", f"--include={name}", patch_path])
    if code:
        result["reason"] = "the test file of the patch does not apply to the drill branch"
        return result
    result["before"] = classify(*run(["make", "test-one", f"FILE={name}"]))
    say(f"before the fix: the new test {result['before']}")
    if result["before"] == "passes":
        result["reason"] = "the new test passes before the fix, so it does not show the defect; the drill is marked failed"
        return result
    if result["before"] != "fails":
        result["reason"] = "the new test did not fail on an assertion before the fix (a collection error or no test ran)"
        return result

    code, _ = run(["git", "apply", f"--exclude={name}", patch_path])
    if code:
        result["reason"] = "the rest of the patch does not apply after the test file"
        return result
    result["after"] = classify(*run(["make", "test-one", f"FILE={name}"]))
    say(f"after the fix: the new test {result['after']}")
    if result["after"] != "passes":
        result["reason"] = "the new test does not pass after the fix"
        return result
    code, _ = run(["make", "test"])
    result["full_suite"] = "passes" if code == 0 else "fails"
    say(f"after the fix: make test {result['full_suite']}")
    if code:
        result["reason"] = "make test fails after the fix"
        return result
    result["ok"] = True
    result["reason"] = "the new test fails before the fix, and it and make test pass after"
    return result


def run_command(argv: list[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"$ {' '.join(argv)}\ntimed out", flush=True)
        return 124, "timed out"
    output = done.stdout + done.stderr
    print(f"$ {' '.join(argv)}\n{output}", flush=True)
    return done.returncode, output


def main(argv=None, *, run: Run = run_command) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("patch", help="the agent's fix.patch")
    parser.add_argument("--result", default="prove.json", help="where to write the result (default prove.json)")
    args = parser.parse_args(argv)
    try:
        text = Path(args.patch).read_text(encoding="utf-8")
    except (OSError, ValueError):
        print("cannot read the patch", file=sys.stderr)
        return 2
    result = prove(text, args.patch, run, lambda line: print(line, flush=True))
    Path(args.result).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(("proved: " if result["ok"] else "NOT proved: ") + result["reason"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
