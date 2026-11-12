"""Public ids (#11): `to_public_id` and `from_public_id`."""

import pytest

from socboard.ids import MAX_ID, from_public_id, to_public_id


def test_to_public_id_is_plain_decimal():
    assert to_public_id(1) == "1"
    assert to_public_id(1234567) == "1234567"
    assert to_public_id(MAX_ID) == "9223372036854775807"


@pytest.mark.parametrize("bad", [0, -1, MAX_ID + 1, True, "1", 1.5, None])
def test_to_public_id_refuses_what_is_not_a_key(bad):
    with pytest.raises(ValueError):
        to_public_id(bad)


@pytest.mark.parametrize("number", [1, 7, 10, 1234567, 2**32, 2**62, MAX_ID])
def test_round_trip(number):
    assert from_public_id(to_public_id(number)) == number


@pytest.mark.parametrize(
    "text",
    [
        "",  # the empty string
        "abc",  # a non-digit
        "12a",
        "1.5",
        "+1",  # a sign
        "-1",
        "01",  # a leading zero
        "0",
        "007",
        "\u0663",  # the Arabic-Indic digit three
        "1\u0663",
        "\uff11",  # a full-width digit one
        " 1",  # whitespace
        "1 ",
        "1\n",
        "\t1",
        str(MAX_ID + 1),  # above 2^63 - 1
        str(2**64),
        "9" * 25,
        "9" * 5000,  # longer than Python's int-from-string limit
        "C-1001",
    ],
)
def test_from_public_id_returns_none(text):
    assert from_public_id(text) is None


def test_from_public_id_boundary():
    assert from_public_id(str(MAX_ID)) == MAX_ID
    assert from_public_id(str(MAX_ID - 1)) == MAX_ID - 1


@pytest.mark.parametrize("value", [None, 1, b"1", ["1"]])
def test_from_public_id_returns_none_for_a_non_string(value):
    assert from_public_id(value) is None
