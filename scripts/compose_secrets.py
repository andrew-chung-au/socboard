"""The first step of `make compose-up` (#221): make local secrets for the Compose stack.

    uv run --locked python scripts/compose_secrets.py

Writes `compose.secrets` in the repository root (git-ignored, mode 0600) with a random value for each
variable the stack requires that is not already set there or in the environment. It only appends
missing names: an existing line is never rewritten or removed, and a second run changes nothing.
It prints the names it added, never a value. It refuses, changing nothing, a symlink, a required
name set to an empty value, and a database password that is not letters and digits (the stack puts
those into database URLs).

The five app names come from the app's own list (`socboard.settings`), so this script holds no second
list of them. Outside `make compose-up` nothing generates a secret: Compose stops on a missing one.
"""

import os
import re
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from socboard.settings import SETTINGS  # noqa: E402

SECRETS_FILE = ROOT / "compose.secrets"
DATABASE_PASSWORDS = ("STACK_PG_SUPERUSER_PASSWORD", "STACK_PG_OWNER_PASSWORD", "STACK_PG_APP_PASSWORD")
NAMES = tuple(setting.name for setting in SETTINGS if setting.make_run) + DATABASE_PASSWORDS
URL_SAFE = re.compile(r"[A-Za-z0-9]+")


def read_file_values(text: str) -> dict[str, str]:
    """NAME=value lines; anything else (comments, other names) is left alone."""
    values = {}
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator and name in NAMES:
            values[name] = value
    return values


def main(environ: Mapping[str, str] | None = None, path: Path = SECRETS_FILE) -> int:
    environ = os.environ if environ is None else environ
    if path.is_symlink():
        print(f"compose secrets: {path.name} is a symbolic link; nothing was written", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    from_file = read_file_values(text)
    problems = []
    missing = []
    for name in NAMES:
        # The environment wins over the file in Compose, so it is checked first.
        value = environ[name] if name in environ else from_file.get(name)
        if value is None:
            missing.append(name)
        elif not value:
            problems.append(f"{name} is set to an empty value; set it or remove it")
        elif name in DATABASE_PASSWORDS and not URL_SAFE.fullmatch(value):
            problems.append(f"{name} must be letters and digits only (it goes into a database URL)")
    if problems:
        for problem in problems:
            print(f"compose secrets: {problem}", file=sys.stderr)
        print("compose secrets: nothing was written", file=sys.stderr)
        return 2
    if not missing:
        print("compose secrets: every name is already set")
        return 0
    lines = "".join(f"{name}={secrets.token_hex(24)}\n" for name in missing)
    if not path.exists():
        # O_EXCL: never replace a file that appeared since the check.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(("" if not text or text.endswith("\n") else "\n") + lines)
    print(f"compose secrets: added {', '.join(missing)} to {path.name}; values are not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
