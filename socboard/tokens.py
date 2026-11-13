"""Signed stateless bearer tokens, standard library only (#13).

A token is `<payload>.<signature>`:

- `<payload>` is the JSON object `{"sub": <public user id>, "exp": <seconds since the epoch>}`,
  base64url encoded without padding.
- `<signature>` is HMAC-SHA256 over the payload text (the encoded `<payload>` exactly as sent)
  with the signing secret, base64url encoded without padding.

`read_token` refuses (returns None for) anything else, without raising: no `.` or more than one,
an empty part, a character outside the base64url alphabet, a length no base64 text can have, a
bad signature (compared in constant time, before the payload is decoded), a payload that is not
a JSON object, a `sub` that `from_public_id` refuses, an `exp` that is missing or not an integer,
a token at or past its `exp`, and an `exp` more than the lifetime ahead of now. The lifetime is
fixed in code (decision record `_docs/decisions/2026-11-12T2255Z-auth-tokens.md`); the functions
take the current time as an argument, so tests never sleep.

The token says who the caller is and nothing else: the role is read from the database on each
request (`socboard.auth.current_user`).
"""

import base64
import binascii
import hashlib
import hmac
import json
import math
import re

from socboard.ids import from_public_id, to_public_id

TOKEN_LIFETIME_SECONDS = 8 * 60 * 60

# Far above any token this module makes (about 100 characters); refused before any work.
MAX_TOKEN_LENGTH = 1024

_B64URL = re.compile(r"[A-Za-z0-9_-]+")


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes | None:
    """The bytes of unpadded base64url `text`, or None."""
    if len(text) % 4 == 1:
        return None
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return None


def _secret_bytes(secret: str) -> bytes:
    return secret.encode("utf-8", "surrogatepass")


def _sign(payload: str, secret: str) -> str:
    mac = hmac.new(_secret_bytes(secret), payload.encode("ascii"), hashlib.sha256)
    return _b64encode(mac.digest())


def create_token(user_id: int, secret: str, now: int | float) -> str:
    """A token for `user_id` that expires `TOKEN_LIFETIME_SECONDS` after `now`."""
    if not secret:
        raise ValueError("The signing secret is empty.")
    claims = {"sub": to_public_id(user_id), "exp": int(now) + TOKEN_LIFETIME_SECONDS}
    payload = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("ascii"))
    return f"{payload}.{_sign(payload, secret)}"


def read_token(token: str, secret: str, now: int | float) -> int | None:
    """The user id the token was made for, or None if the token is refused for any reason."""
    if not secret or not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        return None
    if token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    if not _B64URL.fullmatch(payload) or not _B64URL.fullmatch(signature):
        return None
    if not hmac.compare_digest(signature.encode("ascii"), _sign(payload, secret).encode("ascii")):
        return None
    raw = _b64decode(payload)
    if raw is None:
        return None
    try:
        claims = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return None
    if not isinstance(claims, dict):
        return None
    sub = claims.get("sub")
    exp = claims.get("exp")
    if not isinstance(sub, str) or isinstance(exp, bool) or not isinstance(exp, int):
        return None
    user_id = from_public_id(sub)
    if user_id is None:
        return None
    if now >= exp:
        return None
    # The lifetime holds when a token is read, not only when it is made. Integers only: `exp` can
    # have hundreds of digits, and `exp - now` with a float `now` would overflow.
    if exp > math.floor(now) + TOKEN_LIFETIME_SECONDS:
        return None
    return user_id
