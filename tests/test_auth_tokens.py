"""Signed bearer tokens (#13): `<payload>.<signature>`, HMAC-SHA256, fixed 8-hour lifetime.

The helpers here build tokens with `hmac` and `base64` directly, not with `socboard.tokens`, so a
mistake in the module's own encoding cannot hide in a matching mistake in the test.
"""

import base64
import hashlib
import hmac
import json

import pytest

from socboard import tokens

SECRET = "test-only-signing-secret-0123456789abcdef"
OTHER_SECRET = "test-only-other-secret-fedcba9876543210"
NOW = 1_800_000_000


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def signed(payload_text: str, secret: str = SECRET) -> str:
    mac = hmac.new(secret.encode(), payload_text.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_text}.{b64(mac)}"


def token_for(claims, secret: str = SECRET) -> str:
    raw = claims if isinstance(claims, bytes) else json.dumps(claims).encode()
    return signed(b64(raw), secret)


def test_the_lifetime_is_eight_hours():
    assert tokens.TOKEN_LIFETIME_SECONDS == 8 * 60 * 60


def test_a_token_is_payload_dot_signature_over_the_payload_text():
    token = tokens.create_token(42, SECRET, now=NOW)
    payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims == {"sub": "42", "exp": NOW + 8 * 60 * 60}
    assert token == signed(payload)
    assert "=" not in token


def test_a_token_reads_back_until_just_before_its_exp():
    token = tokens.create_token(42, SECRET, now=NOW)
    assert tokens.read_token(token, SECRET, now=NOW) == 42
    assert tokens.read_token(token, SECRET, now=NOW + tokens.TOKEN_LIFETIME_SECONDS - 1) == 42
    assert tokens.read_token(token, SECRET, now=NOW + tokens.TOKEN_LIFETIME_SECONDS - 0.5) == 42


def test_a_token_at_or_past_its_exp_is_refused():
    token = tokens.create_token(42, SECRET, now=NOW)
    assert tokens.read_token(token, SECRET, now=NOW + tokens.TOKEN_LIFETIME_SECONDS) is None
    assert tokens.read_token(token, SECRET, now=NOW + tokens.TOKEN_LIFETIME_SECONDS + 1) is None


def test_an_exp_exactly_the_lifetime_ahead_is_accepted():
    lifetime = tokens.TOKEN_LIFETIME_SECONDS
    assert tokens.read_token(token_for({"sub": "7", "exp": NOW + lifetime}), SECRET, now=NOW) == 7
    # A float `now`, as `time.time()` gives: create_token at NOW + 0.9 makes exp NOW + lifetime.
    assert tokens.read_token(token_for({"sub": "7", "exp": NOW + lifetime}), SECRET, now=NOW + 0.9) == 7


def test_an_exp_more_than_the_lifetime_ahead_is_refused():
    lifetime = tokens.TOKEN_LIFETIME_SECONDS
    assert tokens.read_token(token_for({"sub": "7", "exp": NOW + lifetime + 1}), SECRET, now=NOW) is None
    assert (
        tokens.read_token(token_for({"sub": "7", "exp": NOW + lifetime + 1}), SECRET, now=NOW + 0.9)
        is None
    )


@pytest.mark.parametrize("exp", [10**500, -(10**500)])
def test_a_huge_exp_is_refused_without_raising(exp):
    token = token_for({"sub": "7", "exp": exp})
    assert len(token) <= tokens.MAX_TOKEN_LENGTH
    assert tokens.read_token(token, SECRET, now=NOW) is None
    assert tokens.read_token(token, SECRET, now=NOW + 0.5) is None


def test_a_hand_built_token_is_accepted():
    # The format is the one the module documents, not just whatever it happens to produce.
    assert tokens.read_token(token_for({"sub": "7", "exp": NOW + 10}), SECRET, now=NOW) == 7


def test_the_signature_is_compared_in_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(tokens.hmac, "compare_digest", spy)
    token = tokens.create_token(42, SECRET, now=NOW)
    assert tokens.read_token(token, SECRET, now=NOW) == 42
    assert tokens.read_token(token, OTHER_SECRET, now=NOW) is None
    assert len(calls) == 2


def test_an_empty_secret_is_refused():
    with pytest.raises(ValueError):
        tokens.create_token(1, "", now=NOW)
    assert tokens.read_token(tokens.create_token(1, SECRET, now=NOW), "", now=NOW) is None


def _valid():
    return tokens.create_token(42, SECRET, now=NOW)


def _payload_changed_under_old_signature():
    _, signature = _valid().split(".")
    payload = b64(json.dumps({"sub": "1", "exp": NOW + 100}).encode())
    return f"{payload}.{signature}"


def _with_padding():
    payload, signature = _valid().split(".")
    padded = payload + "=" * (-len(payload) % 4 or 4)
    return f"{padded}.{signature}"


REFUSED = {
    "empty": "",
    "no dot": _valid().replace(".", ""),
    "two dots": _valid() + ".x",
    "dot only": ".",
    "empty payload": "." + _valid().split(".")[1],
    "empty signature": _valid().split(".")[0] + ".",
    "padding character": _with_padding(),
    "plus sign": _valid().replace(".", "+.", 1),
    "slash": _valid().replace(".", "/.", 1),
    "space": " " + _valid(),
    "non-ascii": _valid() + "é",
    "impossible base64 length": signed("AAAAA"),
    "payload not json": token_for(b"not json"),
    "payload not utf-8": token_for(b"\xff\xfe"),
    "payload a json array": token_for([{"sub": "1", "exp": NOW + 100}]),
    "payload a json string": token_for("sub"),
    "sub missing": token_for({"exp": NOW + 100}),
    "sub an integer": token_for({"sub": 1, "exp": NOW + 100}),
    "sub zero": token_for({"sub": "0", "exp": NOW + 100}),
    "sub negative": token_for({"sub": "-1", "exp": NOW + 100}),
    "sub leading zero": token_for({"sub": "01", "exp": NOW + 100}),
    "sub not digits": token_for({"sub": "abc", "exp": NOW + 100}),
    "sub too large": token_for({"sub": str(2**63), "exp": NOW + 100}),
    "exp missing": token_for({"sub": "1"}),
    "exp a string": token_for({"sub": "1", "exp": "soon"}),
    "exp numeric string": token_for({"sub": "1", "exp": str(NOW + 100)}),
    "exp a float": token_for({"sub": "1", "exp": float(NOW + 100)}),
    "exp a boolean": token_for({"sub": "1", "exp": True}),
    "exp null": token_for({"sub": "1", "exp": None}),
    "exp NaN": token_for(b'{"sub": "1", "exp": NaN}'),
    "exp Infinity": token_for(b'{"sub": "1", "exp": Infinity}'),
    "nested arrays": token_for(b"[" * 300 + b"]" * 300),
    "bad signature": _valid().split(".")[0] + "." + b64(b"x" * 32),
    "truncated signature": _valid()[:-1],
    "payload changed under its old signature": _payload_changed_under_old_signature(),
    "signed with another secret": tokens.create_token(42, OTHER_SECRET, now=NOW),
    "expired": tokens.create_token(42, SECRET, now=NOW - tokens.TOKEN_LIFETIME_SECONDS),
    "too long": "A" * (tokens.MAX_TOKEN_LENGTH + 1) + "." + "A",
}


@pytest.mark.parametrize("name", sorted(REFUSED))
def test_a_malformed_or_forged_token_is_refused_without_raising(name):
    assert tokens.read_token(REFUSED[name], SECRET, now=NOW) is None


@pytest.mark.parametrize("value", [None, 42, b"bytes", ["a.b"]])
def test_a_token_that_is_not_a_string_is_refused(value):
    assert tokens.read_token(value, SECRET, now=NOW) is None
