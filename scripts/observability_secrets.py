"""The first step of `make observability-up` (#293): make Grafana's admin password.

    uv run --locked python scripts/observability_secrets.py

Writes `observability.secrets` in the repository root (git-ignored, mode 0600) with a random
`STACK_GRAFANA_ADMIN_PASSWORD`, unless it is already set there or in the environment. It never rewrites or
removes a line, so a second run changes nothing, and it prints the name it added, never the value. It
refuses, changing nothing, a symbolic link and a name set to an empty value. `compose.secrets` and
`scripts/compose_secrets.py` stay as they are: the observability layer's secret is its own file.
"""

import os
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

NAME = "STACK_GRAFANA_ADMIN_PASSWORD"
SECRETS_FILE = Path(__file__).resolve().parent.parent / "observability.secrets"


def read_file_value(text: str) -> str | None:
    """The value of NAME in a file of NAME=value lines; anything else in it is left alone."""
    found = None
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator and name == NAME:
            found = value
    return found


def main(environ: Mapping[str, str] | None = None, path: Path = SECRETS_FILE) -> int:
    environ = os.environ if environ is None else environ
    if path.is_symlink():
        print(f"observability secrets: {path.name} is a symbolic link; nothing was written", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    value = environ[NAME] if NAME in environ else read_file_value(text)  # the environment wins in Compose
    if value is not None and not value.strip():
        print(f"observability secrets: {NAME} is set to an empty value; set it or remove it", file=sys.stderr)
        print("observability secrets: nothing was written", file=sys.stderr)
        return 2
    if value is not None:
        if not path.exists():  # set in the environment only; Compose still reads the file
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        print("observability secrets: already set")
        return 0
    line = f"{NAME}={secrets.token_hex(24)}\n"
    if not path.exists():
        # O_EXCL: never replace a file that appeared since the check.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(line)
    else:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(("" if not text or text.endswith("\n") else "\n") + line)
    print(f"observability secrets: added {NAME} to {path.name}; the value is not printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
