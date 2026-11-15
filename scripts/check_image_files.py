"""Check the files of the container image (#190), exported to a folder.

    python3 scripts/check_image_files.py <folder>

<folder> holds the image's file system, as `docker export` gives it: the app is under
<folder>/app. Standard library only, so CI runs it before anything is installed.

Exit 0 when nothing is found. Exit 1 with one line per finding, naming the path (relative to
<folder>) and the kind of finding, never a matched value. Exit 2 when the folder has no app/
inside it, or a pattern in the secret list does not compile (nothing is skipped or narrowed).

Under app/, outside the virtual environment (.venv holds other people's code): a file or folder
named like an env file (other than the example one), .git, tests or node_modules, a database or
journal file, or a test_*.py file. In the files the project owns (app/socboard, app/frontend/dist,
app/README.md): a demo credential or hostile fixture, a Google fonts host, a line matching a
pattern in _docs/agent-kit/scripts/secret-patterns.txt, and, under app/frontend/dist, server or
worker output (the names `make frontend-build` refuses).
"""

import fnmatch
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_PATTERNS = ROOT / "_docs" / "agent-kit" / "scripts" / "secret-patterns.txt"

# The patterns `make frontend-build` greps for (Makefile, frontend-build).
DEMO_PATTERNS = (
    re.compile(r"analyst-a-pass|analyst-b-pass|lead-pass|<script>alert\("),
)
FONTS_PATTERN = re.compile(r"fonts\.googleapis\.com|fonts\.gstatic\.com")
SERVER_OUTPUT_NAMES = ("server", "wrangler.json", "nitro.json", "_worker.js", "*.mjs")

FORBIDDEN_DIR_NAMES = {".git": "a .git folder", "tests": "a tests folder", "node_modules": "a node_modules folder"}
DATABASE_PATTERNS = ("*.db", "*.sqlite", "*.sqlite3", "*.sqlite-*", "*.db-journal", "*.db-wal", "*.db-shm", "*-journal")
EXAMPLE_ENV_NAME = ".env.example"


def is_env_name(name: str) -> bool:
    if name == EXAMPLE_ENV_NAME:
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def load_secret_patterns() -> list[tuple[int, re.Pattern[str]]]:
    patterns = []
    for number, line in enumerate(SECRET_PATTERNS.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        patterns.append((number, re.compile(line)))  # a bad pattern raises re.error: exit 2, never skipped
    return patterns


def name_findings(app: Path, root: Path) -> list[str]:
    findings = []
    for current, dirs, files in os.walk(app, followlinks=False):
        current_path = Path(current)
        if current_path == app and ".venv" in dirs:
            dirs.remove(".venv")
        for name in [*dirs, *files]:
            relative = (current_path / name).relative_to(root).as_posix()
            if is_env_name(name):
                findings.append(f"{relative}: named like an env file")
            if name in FORBIDDEN_DIR_NAMES:
                findings.append(f"{relative}: {FORBIDDEN_DIR_NAMES[name]}")
            if name in files:
                if any(fnmatch.fnmatchcase(name, pattern) for pattern in DATABASE_PATTERNS):
                    findings.append(f"{relative}: a database or journal file")
                if fnmatch.fnmatchcase(name, "test_*.py"):
                    findings.append(f"{relative}: a test file")
    return findings


def owned_files(app: Path) -> list[Path]:
    found = []
    readme = app / "README.md"
    if readme.is_file() and not readme.is_symlink():
        found.append(readme)
    for folder in (app / "socboard", app / "frontend" / "dist"):
        for current, _, files in os.walk(folder, followlinks=False):
            found.extend(Path(current) / name for name in files if not (Path(current) / name).is_symlink())
    return found


def content_findings(app: Path, root: Path, secret_patterns: list[tuple[int, re.Pattern[str]]]) -> list[str]:
    findings = []
    for path in owned_files(app):
        relative = path.relative_to(root).as_posix()
        # latin-1 reads every byte, so a binary file is scanned too and nothing is skipped.
        lines = path.read_bytes().decode("latin-1").splitlines()
        if any(pattern.search(line) for line in lines for pattern in DEMO_PATTERNS):
            findings.append(f"{relative}: a demo credential or hostile fixture")
        if any(FONTS_PATTERN.search(line) for line in lines):
            findings.append(f"{relative}: a Google fonts host")
        for number, pattern in secret_patterns:
            if any(pattern.search(line) for line in lines):
                findings.append(f"{relative}: matches secret-patterns.txt line {number}")
    return findings


def server_output_findings(app: Path, root: Path) -> list[str]:
    findings = []
    dist = app / "frontend" / "dist"
    for current, dirs, files in os.walk(dist, followlinks=False):
        for name in [*dirs, *files]:
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in SERVER_OUTPUT_NAMES):
                findings.append(f"{(Path(current) / name).relative_to(root).as_posix()}: server or worker output in the build")
    return findings


def check(root: Path) -> list[str]:
    """One line per finding; empty when the folder is clean. Raises FileNotFoundError or re.error."""
    app = root / "app"
    if not app.is_dir():
        raise FileNotFoundError("no app/ folder")
    secret_patterns = load_secret_patterns()
    return [*name_findings(app, root), *content_findings(app, root, secret_patterns), *server_output_findings(app, root)]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_image_files.py <folder holding the image's files>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    try:
        findings = check(root)
    except FileNotFoundError as error:
        print(f"check_image_files: {error} in the folder given", file=sys.stderr)
        return 2
    except re.error as error:
        print(f"check_image_files: a pattern in the secret list does not compile ({error.msg}); nothing was checked", file=sys.stderr)
        return 2
    for line in findings:
        print(f"check_image_files: {line}", file=sys.stderr)
    if findings:
        return 1
    print("check_image_files: no finding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
