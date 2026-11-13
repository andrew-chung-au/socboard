"""Password hashes (#13): salted scrypt from the standard library, one self-describing string."""

import hashlib
import hmac

import pytest

from socboard import passwords

PASSWORD = "test-only-password-1"

# The first minimum set in the OWASP password storage guidance (scrypt), as recorded in
# _docs/decisions/2026-11-12T2255Z-auth-tokens.md.
OWASP_N, OWASP_R, OWASP_P = 2**17, 8, 1


def test_openssl_supports_scrypt_here():
    # The decision record's trade-off: hashlib.scrypt needs OpenSSL scrypt support. This is the
    # first check in the dev container and in CI.
    assert hasattr(hashlib, "scrypt")
    assert len(hashlib.scrypt(b"x", salt=b"0" * 16, n=2, r=1, p=1, dklen=32)) == 32


def test_the_stored_value_is_self_describing_and_not_the_password():
    stored = passwords.hash_password(PASSWORD)
    assert PASSWORD not in stored
    algorithm, params, salt, digest = stored.split("$")
    assert algorithm == "scrypt"
    assert params == f"n={OWASP_N},r={OWASP_R},p={OWASP_P}"
    assert salt and digest


def test_the_cost_parameters_read_back_from_a_stored_hash_are_owasp_minimums():
    n, r, p, salt, digest = passwords.parse_hash(passwords.hash_password(PASSWORD))
    assert (n, r, p) == (OWASP_N, OWASP_R, OWASP_P)
    assert (passwords.SCRYPT_N, passwords.SCRYPT_R, passwords.SCRYPT_P) == (n, r, p)
    assert len(salt) >= 16
    assert len(digest) == 32


def test_the_same_password_twice_gives_different_salts_and_hashes():
    first = passwords.hash_password(PASSWORD)
    second = passwords.hash_password(PASSWORD)
    assert first != second
    assert passwords.parse_hash(first)[3] != passwords.parse_hash(second)[3]
    assert passwords.verify_password(PASSWORD, first)
    assert passwords.verify_password(PASSWORD, second)


def test_the_hash_is_scrypt_of_the_password_and_salt():
    stored = passwords.hash_password(PASSWORD)
    n, r, p, salt, digest = passwords.parse_hash(stored)
    expected = hashlib.scrypt(
        PASSWORD.encode(), salt=salt, n=n, r=r, p=p, maxmem=2**28, dklen=len(digest)
    )
    assert digest == expected


@pytest.mark.parametrize(
    "attempt", ["", "test-only-password-2", "TEST-ONLY-PASSWORD-1", PASSWORD + " ", " " + PASSWORD]
)
def test_a_different_password_does_not_verify(attempt):
    assert not passwords.verify_password(attempt, passwords.hash_password(PASSWORD))


def test_the_comparison_is_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((type(a), type(b)))
        return real(a, b)

    monkeypatch.setattr(passwords.hmac, "compare_digest", spy)
    stored = passwords.hash_password(PASSWORD)
    assert passwords.verify_password(PASSWORD, stored)
    assert not passwords.verify_password("test-only-wrong-pw", stored)
    assert calls == [(bytes, bytes), (bytes, bytes)]


def test_the_dummy_hash_uses_the_current_parameters():
    n, r, p, salt, digest = passwords.parse_hash(passwords.DUMMY_HASH)
    assert (n, r, p) == (passwords.SCRYPT_N, passwords.SCRYPT_R, passwords.SCRYPT_P)
    assert len(salt) == passwords.SALT_BYTES
    assert len(digest) == passwords.HASH_BYTES


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "plaintext",
        "scrypt$n=131072,r=8,p=1$c2FsdA",
        "bcrypt$n=131072,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=131071,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=1073741824,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=131072,r=8,p=1$AAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=131072,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAA",
        "scrypt$n=131072,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA+$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=131072,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=0131072,r=8,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "scrypt$n=131072,r=8,p=1$ÄAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    ],
)
def test_a_stored_value_that_is_not_a_valid_hash_verifies_false_without_raising(stored):
    with pytest.raises(passwords.PasswordHashFormatError) as error:
        passwords.parse_hash(stored)
    if stored:
        assert stored not in str(error.value)
    assert passwords.verify_password(PASSWORD, stored) is False


def test_a_lone_surrogate_password_is_hashed_not_an_error():
    stored = passwords.hash_password("\ud800-test-only")
    assert passwords.verify_password("\ud800-test-only", stored)
    assert not passwords.verify_password("\ud801-test-only", stored)
