"""Run the backend: `python -m socboard.server` (started by `make backend-run`).

Host and port come from SOCBOARD_HOST and SOCBOARD_PORT. The server binds to localhost only:
any other host is refused before anything is imported or started. The app also needs
SOCBOARD_TOKEN_SECRET (#13): without a usable one, or with an invalid SOCBOARD_DATABASE_URL, the
server prints a message naming the variable (never its value) and exits 2, as for a bad host.
"""

import os
import sys
from collections.abc import Mapping

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class BindError(ValueError):
    """The host or port setting is not acceptable."""


def resolve_bind(environ: Mapping[str, str]) -> tuple[str, int]:
    host = environ.get("SOCBOARD_HOST", DEFAULT_HOST).strip()
    if host not in LOOPBACK_HOSTS:
        raise BindError("SOCBOARD_HOST must be localhost: 127.0.0.1, localhost or ::1")
    raw_port = environ.get("SOCBOARD_PORT", str(DEFAULT_PORT)).strip()
    if not raw_port.isascii() or not raw_port.isdigit():
        raise BindError("SOCBOARD_PORT must be a whole number from 1 to 65535")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise BindError("SOCBOARD_PORT must be a whole number from 1 to 65535")
    return host, port


def main(environ: Mapping[str, str] | None = None) -> int:
    try:
        host, port = resolve_bind(os.environ if environ is None else environ)
    except BindError as error:
        print(f"socboard.server: {error}", file=sys.stderr)
        return 2
    try:
        import uvicorn

        from socboard.app import create_app
        from socboard.auth import ConfigError
        from socboard.database import DatabaseUrlError
    except ImportError as error:
        print(
            f"socboard.server: a backend package is missing ({error}); run make install",
            file=sys.stderr,
        )
        return 1
    try:
        app = create_app(os.environ if environ is None else environ)
    except (ConfigError, DatabaseUrlError) as error:
        # Both messages are fixed text naming the variable, never its value.
        print(f"socboard.server: {error}", file=sys.stderr)
        return 2
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
