"""Request and response models of the HTTP API, named as in `openapi.yaml` (#9).

Field names are the contract's (camelCase). A model here never carries a password hash: `User` is
built field by field from the database row, never from the row object as a whole.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, StringConstraints

from socboard.ids import to_public_id

Role = Literal["analyst", "lead"]
Severity = Literal["Low", "Medium", "High", "Critical"]
AssetCriticality = Literal["Low", "Medium", "High", "Critical"]
Status = Literal["New", "Investigating", "Closed"]
ClosingReason = Literal["true_positive", "false_positive", "duplicate"]


class User(BaseModel):
    id: str
    username: str
    displayName: str
    role: Role

    @classmethod
    def from_row(cls, row) -> "User":
        """The public fields of a `socboard.models.User` row (never `password_hash`)."""
        return cls(
            id=to_public_id(row.id),
            username=row.username,
            displayName=row.display_name,
            role=row.role,
        )


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: User


class CaseSummary(BaseModel):
    """A case as the queue and the ingest answer show it. Times are RFC 3339 in UTC (`Z`)."""

    id: str
    title: str
    alertType: str
    source: str
    asset: str
    assetCriticality: AssetCriticality
    severity: Severity
    status: Status
    owner: User | None
    alertTimestamp: datetime
    ingestedAt: datetime
    closingReason: ClosingReason | None


# RFC 3339 `date-time`: seconds and an offset are required. Plain ASCII digits only; Python's own
# ISO parser also takes a date alone, no offset and other forms that the contract refuses.
_RFC3339 = re.compile(
    r"(?P<stamp>[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-5][0-9]:[0-5][0-9])"
    r"(?:\.(?P<fraction>[0-9]+))?"
    r"(?P<offset>[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
_BAD_TIMESTAMP = "The timestamp must be an RFC 3339 date-time with an offset."


def _parse_timestamp(value: object) -> datetime:
    """The instant in UTC. Strict, because pydantic's lax datetime takes numbers and dates."""
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(_BAD_TIMESTAMP)
    # Rebuilt with exactly six fraction digits (extra digits cut), so the parse never depends on
    # which fraction lengths this Python version accepts.
    fraction = (match["fraction"] or "")[:6].ljust(6, "0")
    offset = "+00:00" if match["offset"] in ("Z", "z") else match["offset"]
    try:
        parsed = datetime.fromisoformat(f"{match['stamp'].upper()}.{fraction}{offset}")
        # OverflowError: the UTC instant is outside years 1 to 9999 (for example 0001-01-01+10:00).
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError(_BAD_TIMESTAMP) from None


def _storable(text: str) -> str:
    """Refuse a lone surrogate (a JSON `\\ud800` escape): the database driver cannot store it."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("The text is not valid Unicode.") from None
    return text


Text200 = Annotated[
    str, StringConstraints(min_length=1, max_length=200), AfterValidator(_storable)
]
Text10000 = Annotated[
    str, StringConstraints(min_length=1, max_length=10_000), AfterValidator(_storable)
]


class IngestRequest(BaseModel):
    """The ingest body (`openapi.yaml` `IngestRequest`, limits from #86). Unknown keys, such as a
    `severity` or `status` from the caller, are ignored."""

    source: Text200
    type: Text200
    asset: Text200
    timestamp: Annotated[datetime, BeforeValidator(_parse_timestamp)]
    title: Text200
    description: Text10000
