"""The one function that writes audit entries (#11).

`append_audit` is the only code that creates an `AuditEntry` (a test parses `socboard/` and checks
it). It adds the row to the caller's session and flushes; it never commits, so the audit entry and
the change it records succeed or fail together.
"""

from datetime import UTC, datetime

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy.orm import Session  # noqa: E402

from socboard.domain import AUDIT_ACTIONS  # noqa: E402
from socboard.models import AuditEntry  # noqa: E402

UNKNOWN_ACTION_MESSAGE = "Unknown audit action."


def append_audit(
    session: Session,
    *,
    case_id: int,
    actor_id: int | None,
    action: str,
    detail: str,
    at: datetime | None = None,
) -> AuditEntry:
    """Add one audit entry to `session`, flush, and return it. `actor_id` None means "system".

    An unknown `action` raises ValueError before the database is touched. `at` defaults to now,
    in UTC.
    """
    if not isinstance(action, str) or action not in AUDIT_ACTIONS:
        raise ValueError(UNKNOWN_ACTION_MESSAGE)
    entry = AuditEntry(
        case_id=case_id,
        actor_id=actor_id,
        action=action,
        detail=detail,
        at=datetime.now(UTC) if at is None else at,
    )
    session.add(entry)
    session.flush()
    return entry
