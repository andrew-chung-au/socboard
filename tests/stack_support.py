"""Running the journeys against a live Compose stack (#229, D12).

`SOCBOARD_BASE_URL` names the stack's address (`make test-stack`; the proxy's, `http://127.0.0.1:8080`
for `make compose-up`). Unset or empty, every test runs in process exactly as before. Set, the journeys
send HTTP requests to it, and the tests marked `stack_only` run too.

The tests ingest alerts and claim cases, so the address must be plain `http` on the loopback: no
other host, no credentials, no query, no fragment, no path. A refused value stops the run with fixed
text that does not print it. The two other variables belong to the live tests only: `STACK_APP_URL`
(the app's own published port, same rule) and `STACK_SCHEMA_FILE` (the image's schema, as JSON).
"""

import os
from collections.abc import Mapping
from urllib.parse import urlsplit

BASE_URL_VAR = "SOCBOARD_BASE_URL"
APP_URL_VAR = "STACK_APP_URL"
SCHEMA_FILE_VAR = "STACK_SCHEMA_FILE"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
RETRY_AFTER_LIMIT = 65  # seconds; the ingest window is 60


class StackSettingError(ValueError):
    """A stack test variable is missing or not usable. The message names the variable, never its value."""


def checked_url(name: str, value: str) -> str:
    """`value` without a trailing slash, or raise naming `name` only."""
    refused = StackSettingError(f"{name} must be a plain http URL on 127.0.0.1, localhost or ::1, with no path, user, query or fragment.")
    if value != value.strip() or any(ord(character) <= 32 for character in value):
        raise refused
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018  (a bad port raises ValueError here)
    except ValueError:
        raise refused from None
    if (
        parts.scheme != "http"
        or parts.hostname not in LOOPBACK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
        or parts.path not in ("", "/")
    ):
        raise refused
    return value.rstrip("/")


def base_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The checked live address, or None when the run is in process."""
    environ = os.environ if environ is None else environ
    value = environ.get(BASE_URL_VAR, "")
    return checked_url(BASE_URL_VAR, value) if value else None


def is_live(environ: Mapping[str, str] | None = None) -> bool:
    return base_url(environ) is not None


def required(name: str, environ: Mapping[str, str] | None = None) -> str:
    """A variable the live tests need; a missing or empty one fails naming it."""
    environ = os.environ if environ is None else environ
    value = environ.get(name, "")
    if not value.strip():
        raise StackSettingError(f"{name} must be set for a live stack run.")
    return value


def load_schema_file(environ: Mapping[str, str] | None = None) -> dict:
    """The image's `create_app().openapi()` as JSON, from the file `STACK_SCHEMA_FILE` names.

    A missing variable, an unreadable file or text that is not a JSON object fails naming the variable,
    never the path or the content.
    """
    import json
    from pathlib import Path

    environ = os.environ if environ is None else environ
    path = required(SCHEMA_FILE_VAR, environ)
    try:
        schema = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise StackSettingError(f"{SCHEMA_FILE_VAR} must name a readable file that holds the image's schema as JSON.") from None
    if not isinstance(schema, dict) or not isinstance(schema.get("paths"), dict):
        raise StackSettingError(f"{SCHEMA_FILE_VAR} must name a readable file that holds the image's schema as JSON.")
    return schema


def live_client(environ: Mapping[str, str] | None = None, url: str | None = None):
    """An HTTP client for the stack: a timeout, no redirect followed."""
    import httpx2

    # trust_env=False: the key and the passwords must not go through a proxy named in the environment.
    return httpx2.Client(base_url=url or base_url(environ), timeout=10, follow_redirects=False, trust_env=False)
