"""`UtcDateTime`: the one timestamp column type.

Write: refuses a datetime with no time zone (fixed message, no value in it) and converts an aware
one to UTC. Read: returns an aware datetime with a zero UTC offset. A value with no zone (SQLite
stores none) gets UTC attached; a value that has a zone (Postgres `timestamptz`) is converted, not
relabelled. Plain `DateTime(timezone=True)` returns naive values on SQLite, which is why this type
exists.

Compare timestamps only through the column or a bind parameter typed `UtcDateTime`, never an
untyped `text()` parameter.
"""

from datetime import UTC, datetime

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import DateTime  # noqa: E402
from sqlalchemy.types import TypeDecorator  # noqa: E402

NAIVE_MESSAGE = "A timestamp must include a time zone."
NOT_DATETIME_MESSAGE = "A timestamp must be a datetime."


class UtcDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(NOT_DATETIME_MESSAGE)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(NAIVE_MESSAGE)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
