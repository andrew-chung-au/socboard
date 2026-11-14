"""The first step of `make run` (#22): check every variable the stack needs, by the app's own rules.

    .venv/bin/python scripts/run_preflight.py

Each verdict comes from the function the backend or `make seed` uses for that variable, so this
script holds no length or format rule of its own and can never be looser or stricter than they
are. `make run` also requires the ingest key, which the backend alone may start without, and
port 8000, the one port the frontend dev server's proxy reaches.
Exit 0 when every variable passes; exit 2 with one line per failing variable, naming it and never
printing its value.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from socboard import ingest  # noqa: E402
from socboard.auth import SECRET_ENV_NAME, ConfigError, resolve_token_secret  # noqa: E402
from socboard.database import DatabaseUrlError, resolve_url  # noqa: E402
from socboard.seed import USERS, read_seed_passwords  # noqa: E402
from socboard.server import BindError, resolve_bind  # noqa: E402

SEED_VARIABLES = tuple(variable for _, _, _, variable in USERS)
REQUIRED = (SECRET_ENV_NAME, *SEED_VARIABLES, ingest.KEY_ENV_NAME)
INGEST_MESSAGE = (
    f"{ingest.KEY_ENV_NAME} must be set to at least {ingest.MIN_KEY_LENGTH} characters "
    "(make run needs ingest open)"
)
# Not an app rule: the dev server's /api proxy is fixed to 127.0.0.1:8000 (frontend/vite.config.ts).
FRONTEND_PROXY_PORT = 8000
PORT_MESSAGE = (
    f"SOCBOARD_PORT must be {FRONTEND_PROXY_PORT} or unset for make run: the frontend dev server "
    f"sends /api to 127.0.0.1:{FRONTEND_PROXY_PORT} (frontend/vite.config.ts)"
)


def check(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """(variable, message) for every failing variable; empty when all pass."""
    problems: list[tuple[str, str]] = []
    try:
        resolve_token_secret(environ)
    except ConfigError as error:
        problems.append((SECRET_ENV_NAME, str(error)))
    found, seed_problems = read_seed_passwords(environ)
    failing = [variable for username, _, _, variable in USERS if username not in found]
    problems.extend(zip(failing, seed_problems))
    # The resolver logs its own warning when the key is missing; this script reports it instead.
    previous = ingest.logger.disabled
    ingest.logger.disabled = True
    try:
        key = ingest.resolve_ingest_key(environ)
    finally:
        ingest.logger.disabled = previous
    if key is None:
        problems.append((ingest.KEY_ENV_NAME, INGEST_MESSAGE))
    try:
        resolve_url(environ)
    except DatabaseUrlError as error:
        problems.append(("SOCBOARD_DATABASE_URL", str(error)))
    # resolve_bind stops at the first bad setting, so ask it about each variable on its own.
    for name in ("SOCBOARD_HOST", "SOCBOARD_PORT"):
        try:
            _, port = resolve_bind({name: environ[name]} if name in environ else {})
        except BindError as error:
            problems.append((name, str(error)))
            continue
        if name == "SOCBOARD_PORT" and port != FRONTEND_PROXY_PORT:
            problems.append((name, PORT_MESSAGE))
    return problems


def main() -> int:
    problems = check(os.environ)
    for _, message in problems:
        print(f"make run: {message}", file=sys.stderr)
    if problems:
        print("make run: nothing was started", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
