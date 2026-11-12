"""Password hashes: salted scrypt from the standard library (#13).

A stored hash is one self-describing string:

    scrypt$n=131072,r=8,p=1$<salt>$<hash>

`<salt>` (16 random bytes) and `<hash>` (32 bytes) are base64url without padding. The cost
parameters travel with each hash, so they can be raised later: `verify_password` reads them from
the stored string, and a new hash uses the constants below. The parameters are the first of the
minimum sets in the OWASP password storage guidance (N=2^17, r=8, p=1, about 128 MiB); the
decision record is `_docs/decisions/2026-11-12T2255Z-auth-tokens.md`.

Nothing here logs, and no function puts a password or a hash into an exception message.
"""

import base64
import binascii
import hashlib
import hmac
import re
import secrets

ALGORITHM = "scrypt"
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
HASH_BYTES = 32

# Bounds for parameters read back from a stored string: a damaged or hostile value must not make
# a verify allocate gigabytes or run for minutes. The current constants sit inside them.
_MAX_N = 2**20
_MAX_R = 16
_MAX_P = 16
_MAX_MEMORY = 128 * _MAX_N * _MAX_R

_PARAMS = re.compile(r"n=([1-9][0-9]{0,9}),r=([1-9][0-9]{0,2}),p=([1-9][0-9]{0,2})")
_B64URL = re.compile(r"[A-Za-z0-9_-]+")

# A hash of a random value nobody knows, made with the parameters above. Login verifies the
# submitted password against it when the username is unknown, so an unknown username costs the
# same work as a wrong password. `tests/test_auth_passwords.py` checks it still uses the current
# parameters, so raising them means making a new one with `hash_password`.
DUMMY_HASH = (
    "scrypt$n=131072,r=8,p=1$"
    "NgIft_yIDm69X5Da6FbdBg$uF6KqlUtMuElWZlQLnxxUT4xXSvDT8HrTkAO_xee7a4"
)


class PasswordHashFormatError(ValueError):
    """A stored hash is not in the format above. The message never holds the value."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    if not _B64URL.fullmatch(text) or len(text) % 4 == 1:
        raise PasswordHashFormatError("Not a valid password hash.")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise PasswordHashFormatError("Not a valid password hash.") from None


def _password_bytes(password: str) -> bytes:
    # `surrogatepass`: a JSON body can carry a lone surrogate (`"\ud800"`), which strict UTF-8
    # refuses; this keeps every str hashable instead of raising a 500.
    return password.encode("utf-8", "surrogatepass")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        _password_bytes(password),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=2 * 128 * n * r * p + 2**20,
        dklen=HASH_BYTES,
    )


def hash_password(password: str) -> str:
    """A new self-describing hash of `password` with a fresh random salt."""
    if not isinstance(password, str):
        raise TypeError("password must be a str")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    params = f"n={SCRYPT_N},r={SCRYPT_R},p={SCRYPT_P}"
    return f"{ALGORITHM}${params}${_b64encode(salt)}${_b64encode(digest)}"


def parse_hash(stored: str) -> tuple[int, int, int, bytes, bytes]:
    """`(n, r, p, salt, hash)` from a stored string, or PasswordHashFormatError."""
    if not isinstance(stored, str) or not stored.isascii():
        raise PasswordHashFormatError("Not a valid password hash.")
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != ALGORITHM:
        raise PasswordHashFormatError("Not a valid password hash.")
    match = _PARAMS.fullmatch(parts[1])
    if match is None:
        raise PasswordHashFormatError("Not a valid password hash.")
    n, r, p = (int(group) for group in match.groups())
    if n < 2 or n & (n - 1) or n > _MAX_N or r > _MAX_R or p > _MAX_P:
        raise PasswordHashFormatError("Not a valid password hash.")
    if 128 * n * r * p > _MAX_MEMORY:
        raise PasswordHashFormatError("Not a valid password hash.")
    salt = _b64decode(parts[2])
    digest = _b64decode(parts[3])
    if len(salt) < SALT_BYTES or len(digest) != HASH_BYTES:
        raise PasswordHashFormatError("Not a valid password hash.")
    return n, r, p, salt, digest


def verify_password(password: str, stored: str) -> bool:
    """True when `password` matches `stored`; the hashes are compared in constant time.

    A stored value that is not a valid hash verifies as False (never an exception), after the
    same scrypt work against the dummy hash, so it costs what a real verify costs.
    """
    if not isinstance(password, str):
        return False
    try:
        n, r, p, salt, expected = parse_hash(stored)
        valid = True
    except PasswordHashFormatError:
        n, r, p, salt, expected = parse_hash(DUMMY_HASH)
        valid = False
    actual = _scrypt(password, salt, n, r, p)
    return hmac.compare_digest(actual, expected) and valid
