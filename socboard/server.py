"""Run the backend: `python -m socboard.server` (started by `make backend-run`).

Host and port come from SOCBOARD_HOST and SOCBOARD_PORT. The server binds to localhost only:
any other host is refused before anything is imported or started. The one command-line flag,
--allow-non-loopback-host, also accepts an IP address in SOCBOARD_HOST; only the container image
passes it, and no environment variable can switch it on (#142). The app also needs
SOCBOARD_TOKEN_SECRET (#13): without a usable one, or with an invalid SOCBOARD_DATABASE_URL, the
server prints a message naming the variable (never its value) and exits 2, as for a bad host.
In deployed mode (`SOCBOARD_MODE=deployed`, #141) the same happens for a missing or unusable
ingest key or database URL; see `socboard.settings`.
"""

import argparse
import ipaddress
import os
import sys
from collections.abc import Mapping, Sequence

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class BindError(ValueError):
    """The host or port setting is not acceptable."""


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return "%" not in host  # no IPv6 zone suffix


def resolve_bind(environ: Mapping[str, str], allow_non_loopback: bool = False) -> tuple[str, int]:
    host = environ.get("SOCBOARD_HOST", DEFAULT_HOST).strip()
    if host not in LOOPBACK_HOSTS:
        if not allow_non_loopback:
            raise BindError("SOCBOARD_HOST must be localhost: 127.0.0.1, localhost or ::1")
        if not _is_ip_address(host):
            raise BindError(
                "SOCBOARD_HOST must be an IP address, or localhost, when --allow-non-loopback-host is given"
            )
    raw_port = environ.get("SOCBOARD_PORT", str(DEFAULT_PORT)).strip()
    if not raw_port.isascii() or not raw_port.isdigit():
        raise BindError("SOCBOARD_PORT must be a whole number from 1 to 65535")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise BindError("SOCBOARD_PORT must be a whole number from 1 to 65535")
    return host, port


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _ArgumentError(message)


def parse_flags(argv: Sequence[str]) -> bool | None:
    """Return the flag's value, or None after printing a usage line (the caller exits 2)."""
    parser = _Parser(prog="python -m socboard.server", add_help=False, allow_abbrev=False)
    parser.add_argument("--allow-non-loopback-host", action="store_true")
    try:
        return parser.parse_args(list(argv)).allow_non_loopback_host
    except _ArgumentError:
        # The argparse message can echo what was typed, so print fixed text only.
        print(parser.format_usage().rstrip(), file=sys.stderr)
        print("socboard.server: unrecognized or invalid argument", file=sys.stderr)
        return None


def main(environ: Mapping[str, str] | None = None, argv: Sequence[str] = ()) -> int:
    allow_non_loopback = parse_flags(argv)
    if allow_non_loopback is None:
        return 2
    try:
        host, port = resolve_bind(os.environ if environ is None else environ, allow_non_loopback)
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
    raise SystemExit(main(argv=sys.argv[1:]))
