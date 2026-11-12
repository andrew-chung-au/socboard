"""`UtcDateTime`, the one timestamp column type (#11)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from data_support import (
    insert_alert,
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
)
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from socboard.db_types import NAIVE_MESSAGE, NOT_DATETIME_MESSAGE, UtcDateTime
from socboard.models import Alert

PLUS_TEN = timezone(timedelta(hours=10))
MINUS_FIVE = timezone(timedelta(hours=-5))


# --- the read path called directly (SQLite never returns an aware value, Postgres does) -------


def test_read_attaches_utc_to_a_value_with_no_zone():
    value = UtcDateTime().process_result_value(datetime(2026, 11, 12, 9, 0), None)
    assert value == datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    assert value.utcoffset() == timedelta(0)


def test_read_converts_an_aware_plus_ten_value_and_does_not_relabel_it():
    aware = datetime(2026, 11, 12, 19, 0, tzinfo=PLUS_TEN)
    value = UtcDateTime().process_result_value(aware, None)
    assert value.utcoffset() == timedelta(0)
    assert value == aware  # the same instant
    assert (value.year, value.month, value.day, value.hour) == (2026, 11, 12, 9)


def test_read_keeps_none():
    assert UtcDateTime().process_result_value(None, None) is None


# --- the write path called directly -----------------------------------------------------------


def test_write_converts_to_utc():
    aware = datetime(2026, 11, 12, 19, 0, tzinfo=PLUS_TEN)
    value = UtcDateTime().process_bind_param(aware, None)
    assert value.utcoffset() == timedelta(0)
    assert value == aware
    assert value.hour == 9


def test_write_refuses_a_naive_value_with_fixed_text_that_holds_no_value():
    naive = datetime(2031, 7, 8, 1, 2, 3)
    with pytest.raises(ValueError) as caught:
        UtcDateTime().process_bind_param(naive, None)
    assert str(caught.value) == NAIVE_MESSAGE
    assert "2031" not in str(caught.value)


def test_write_refuses_a_value_that_is_not_a_datetime():
    with pytest.raises(TypeError) as caught:
        UtcDateTime().process_bind_param("2026-11-12T09:00:00Z", None)
    assert str(caught.value) == NOT_DATETIME_MESSAGE


def test_write_keeps_none():
    assert UtcDateTime().process_bind_param(None, None) is None


# --- through SQLite ---------------------------------------------------------------------------


def test_a_plus_ten_value_is_read_back_as_the_same_instant_with_offset_zero(migrated_engine):
    written = datetime(2026, 11, 12, 19, 0, tzinfo=PLUS_TEN)
    with migrated_engine.begin() as conn:
        alert_id = insert_alert(conn, alert_timestamp=written, ingested_at=written)
    with migrated_engine.connect() as conn:
        row = conn.execute(
            select(Alert.alert_timestamp, Alert.ingested_at).where(Alert.id == alert_id)
        ).one()
        raw = conn.exec_driver_sql("SELECT alert_timestamp FROM alerts").scalar()
    for value in row:
        assert value.utcoffset() == timedelta(0)
        assert value == written
    assert str(raw).startswith("2026-11-12 09:00:00")  # stored as UTC wall time


def test_a_naive_value_is_refused_and_the_error_holds_no_value(migrated_engine):
    naive = datetime(2031, 7, 8, 1, 2, 3)
    with pytest.raises(StatementError) as caught:
        with migrated_engine.begin() as conn:
            insert_alert(conn, alert_timestamp=naive)
    assert "2031" not in str(caught.value)
    assert NAIVE_MESSAGE in str(caught.value)
    with migrated_engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM alerts").scalar() == 0


def test_every_timestamp_column_uses_the_type():
    from socboard.models import AuditEntry, Note

    for column in (
        Alert.__table__.c.alert_timestamp,
        Alert.__table__.c.ingested_at,
        Note.__table__.c.created_at,
        AuditEntry.__table__.c.at,
    ):
        assert isinstance(column.type, UtcDateTime), column.name


def test_a_comparison_through_the_column_uses_the_instant(migrated_engine):
    earlier = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
    later = datetime(2026, 11, 12, 20, 0, tzinfo=PLUS_TEN)  # 10:00 UTC
    with migrated_engine.begin() as conn:
        first = insert_alert(conn, alert_timestamp=later)
        second = insert_alert(conn, alert_timestamp=earlier)
    with migrated_engine.connect() as conn:
        ordered = conn.execute(select(Alert.id).order_by(Alert.alert_timestamp)).scalars().all()
        after_nine_utc = (
            conn.execute(
                select(Alert.id).where(Alert.alert_timestamp > datetime(2026, 11, 12, 4, 0, tzinfo=MINUS_FIVE))
            )
            .scalars()
            .all()
        )
    assert ordered == [second, first]
    assert after_nine_utc == [first]  # 04:00 at -05:00 is 09:00 UTC
