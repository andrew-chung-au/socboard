"""Public ids: a primary key as a decimal string, and back.

The contract calls ids opaque, so clients must not rely on this form. It keeps ascending order for
audit entries and notes. `from_public_id` returns None for anything that is not a key the database
could have assigned; a later route answers that with 404 `not_found` instead of a 500.
"""

MAX_ID = 2**63 - 1
_MAX_DIGITS = len(str(MAX_ID))


def to_public_id(n: int) -> str:
    """Decimal digits, no prefix, no padding."""
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_ID:
        raise ValueError("Not a valid primary key.")
    return str(n)


def from_public_id(text: str) -> int | None:
    """The key for `text`, or None.

    None for: the empty string, a non-digit, a sign, a leading zero, a non-ASCII digit, whitespace
    and any number above 2^63 - 1 (which would not fit a 64-bit column).
    """
    if not isinstance(text, str):
        return None
    if not text.isascii() or not text.isdigit():
        return None
    if text[0] == "0":
        return None
    if len(text) > _MAX_DIGITS:
        return None
    number = int(text)
    if number > MAX_ID:
        return None
    return number
