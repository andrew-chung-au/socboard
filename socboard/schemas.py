"""Request and response models of the HTTP API, named as in `openapi.yaml` (#9).

Field names are the contract's (camelCase). A model here never carries a password hash: `User` is
built field by field from the database row, never from the row object as a whole.
"""

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, StringConstraints

from socboard.domain import AUDIT_ACTIONS
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


class Note(BaseModel):
    id: str
    author: User
    text: str
    createdAt: datetime


class CaseDetail(CaseSummary):
    """`CaseSummary` plus the alert's description, the severity rule and the notes (#15)."""

    description: str
    severityRule: str
    notes: list[Note]


class QueueResponse(BaseModel):
    cases: list[CaseSummary]
    servedAt: datetime


# RFC 3339 `date-time`: seconds and an offset are required. Plain ASCII digits only; Python's own
# ISO parser also takes a date alone, no offset and other forms that the contract refuses.
_RFC3339 = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})[Tt]"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]+))?"
    r"(?P<offset>[Zz]|(?P<sign>[+-])(?P<off_hour>[0-9]{2}):(?P<off_minute>[0-9]{2}))"
)
_BAD_TIMESTAMP = "The timestamp must be an RFC 3339 date-time with an offset."


def _parse_timestamp(value: object) -> datetime:
    """The instant in UTC. Strict, because pydantic's lax datetime takes numbers and dates."""
    match = _RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(_BAD_TIMESTAMP)
    # The parts go to `datetime` and `timezone` as integers, so they refuse hour 24, a minute or
    # second over 59, a day or month out of range and an offset of a day or more. Python's ISO
    # parser is not used: it takes hour 24. Extra fraction digits are cut to microseconds.
    microsecond = int((match["fraction"] or "")[:6].ljust(6, "0"))
    try:
        if match["sign"] is None:
            zone = UTC
        else:
            off_hour, off_minute = int(match["off_hour"]), int(match["off_minute"])
            if off_minute > 59:  # `timedelta` would take it as carry into the hour
                raise ValueError(_BAD_TIMESTAMP)
            shift = timedelta(hours=off_hour, minutes=off_minute)
            zone = timezone(-shift if match["sign"] == "-" else shift)
        parsed = datetime(
            int(match["year"]),
            int(match["month"]),
            int(match["day"]),
            int(match["hour"]),
            int(match["minute"]),
            int(match["second"]),
            microsecond,
            tzinfo=zone,
        )
        # OverflowError: the UTC instant is outside years 1 to 9999 (for example 0001-01-01+10:00).
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        raise ValueError(_BAD_TIMESTAMP) from None


def _storable(text: str) -> str:
    """Refuse a lone surrogate (JSON `\\ud800`) or a NUL: the database cannot store them."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("The text is not valid Unicode.") from None
    if "\x00" in text:  # Postgres rejects NUL in a text column; SQLite accepts it
        raise ValueError("The text is not valid Unicode.")
    return text


Text200 = Annotated[
    str, StringConstraints(min_length=1, max_length=200), AfterValidator(_storable)
]
Text10000 = Annotated[
    str, StringConstraints(min_length=1, max_length=10_000), AfterValidator(_storable)
]


# Notes (#18)
NOTE_MAX_LENGTH = 5_000


def _note_text(text: str) -> str:
    """The length limit is a validator, not `max_length`, so the generated schema keeps the
    contract's `NoteRequest` (no length keyword). Over the limit is a schema 422 (checked before
    the 404, as the contract orders it). Emptiness is a domain rule in the handler."""
    if len(text) > NOTE_MAX_LENGTH:
        raise ValueError("The note is too long.")
    return _storable(text)


class NoteRequest(BaseModel):
    text: Annotated[str, AfterValidator(_note_text)]


class TransitionRequest(BaseModel):
    to: Status
    # Optional but not nullable, as in the contract: an explicit null is a schema 422.
    closingReason: ClosingReason = None  # type: ignore[assignment]


class IngestRequest(BaseModel):
    """The ingest body (`openapi.yaml` `IngestRequest`, limits from #86). Unknown keys, such as a
    `severity` or `status` from the caller, are ignored."""

    source: Text200
    type: Text200
    asset: Text200
    timestamp: Annotated[datetime, BeforeValidator(_parse_timestamp)]
    title: Text200
    description: Text10000


# Audit trail (#19)
AuditAction = Literal[*AUDIT_ACTIONS]


class AuditEntry(BaseModel):
    """One audit entry. `actor` is the user, or the string "system" (an ingest has no user)."""

    id: str
    at: datetime
    actor: User | Literal["system"]
    action: AuditAction
    detail: str
