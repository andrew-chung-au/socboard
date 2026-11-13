"""The allowed values of every enumerated column, defined once.

They are exactly the contract's (`openapi.yaml`, #9); `tests/test_data_domain.py` compares each list
with the matching enum in that file, so a change on either side fails a test. The models build
their CHECK constraints from these lists. The migration files do NOT import this module: they hold
the CHECK text as literals, and a test inserts every allowed value into the migrated database.

This module holds data only, no business logic and no database import.
"""

ROLES = ("analyst", "lead")
STATUSES = ("New", "Investigating", "Closed")
CLOSING_REASONS = ("true_positive", "false_positive", "duplicate")
SEVERITIES = ("Low", "Medium", "High", "Critical")
ASSET_CRITICALITIES = ("Low", "Medium", "High", "Critical")
AUDIT_ACTIONS = ("ingested", "claimed", "status_changed", "note_added", "reassigned")

STATUS_NEW = "New"
STATUS_CLOSED = "Closed"
ACTION_INGESTED = "ingested"

# The contract's schema name -> the allowed values here (used by the comparison test).
ENUMS = {
    "Role": ROLES,
    "Status": STATUSES,
    "ClosingReason": CLOSING_REASONS,
    "Severity": SEVERITIES,
    "AssetCriticality": ASSET_CRITICALITIES,
    "AuditAction": AUDIT_ACTIONS,
}


def sql_literal(value: str) -> str:
    """A quoted literal for a fixed value from this module (letters and underscores only)."""
    if not value or not value.replace("_", "").isalpha() or not value.isascii():
        raise ValueError("A domain value must be ASCII letters and underscores.")
    return "'" + value + "'"


def in_check(column: str, values: tuple[str, ...]) -> str:
    """CHECK text `column IN ('a', 'b')` built from fixed constants, never from input."""
    return column + " IN (" + ", ".join(sql_literal(value) for value in values) + ")"
