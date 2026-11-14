"""Fail unless the running interpreter's major.minor equals the one in `.python-version` (#100).

    uv run --locked python scripts/check_python_version.py

The file must hold the minor version only (`3.12`): a missing, empty, multi-line or patch-version
(`3.12.3`) file fails too. Exit 0 on a match; exit 1 with one message naming both versions.
"""

import re
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent.parent / ".python-version"


def check(version_file: Path, running: tuple[int, int]) -> str | None:
    """The failure message, or None when the interpreter matches the file."""
    try:
        text = version_file.read_text()
    except OSError:
        return f"{version_file.name} is missing or unreadable; it must hold the minor version, e.g. 3.12"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or not re.fullmatch(r"\d+\.\d+", lines[0]):
        return f"{version_file.name} must hold one line with the minor version only, e.g. 3.12; got {text!r}"
    if lines[0] != "{}.{}".format(*running):
        return "Python {}.{} is running but {} says {}".format(*running, version_file.name, lines[0])
    return None


if __name__ == "__main__":
    print(f"Python {sys.version.split()[0]}")
    problem = check(VERSION_FILE, sys.version_info[:2])
    if problem:
        print(problem, file=sys.stderr)
        sys.exit(1)
