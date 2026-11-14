"""Two places where Postgres and SQLite differ, each asserted for the database the run uses (#192).

The run's database is SQLite by default and Postgres when the test variable in `db_target.py` is set.
The other two differences #192 names run on both databases already, unchanged: the timestamp
round-trip (`test_data_timestamps.py`) and the NULL ordering of the closed list
(`test_closed_cases_api.py::test_a_closed_case_with_no_closing_entry_is_listed_last_by_id`).
"""

import pytest
from data_support import (  # noqa: F401  (fixtures)
    insert_case,
    insert_note,
    insert_user,
    isolated_database_env,
    migrated_engine,
    no_default_database_file,
)
from db_target import is_postgres
from sqlalchemy import BigInteger, Integer, inspect, insert, select
from sqlalchemy.exc import DataError

from socboard.models import Note, User

ID_TABLES = ("users", "assets", "alerts", "cases", "notes", "audit_entries")
BIG_ID = 2**62  # above the 32-bit range, inside the signed 64-bit range


@pytest.mark.parametrize("table", ID_TABLES)
def test_every_id_column_has_the_type_this_database_should_give_it(migrated_engine, table):
    column = next(c for c in inspect(migrated_engine).get_columns(table) if c["name"] == "id")
    if is_postgres():
        assert isinstance(column["type"], BigInteger), column["type"]  # 64-bit (BIGSERIAL)
    else:
        assert isinstance(column["type"], Integer), column["type"]  # SQLite's INTEGER is 64-bit already


def test_an_id_above_the_32_bit_range_is_stored_and_read_back_unchanged(migrated_engine):
    row = dict(id=BIG_ID, username="big-id-user", display_name="Big", role="analyst", password_hash="h")
    with migrated_engine.begin() as conn:
        conn.execute(insert(User.__table__).values(**row))
    with migrated_engine.connect() as conn:
        read = conn.execute(select(User.__table__.c.id).where(User.__table__.c.username == "big-id-user")).scalar_one()
    assert read == BIG_ID
    assert type(read) is int


def test_a_nul_character_in_text_is_refused_on_postgres_and_kept_on_sqlite(migrated_engine):
    text_with_nul = "before\x00after"
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
    if is_postgres():
        # Postgres text cannot hold a NUL; the driver refuses it before it is sent.
        with pytest.raises(DataError, match="NUL"):
            with migrated_engine.begin() as conn:
                insert_note(conn, case_id=case_id, author_id=user_id, text=text_with_nul)
        with migrated_engine.connect() as conn:
            assert conn.execute(select(Note.__table__.c.id)).all() == []  # nothing was written
    else:
        with migrated_engine.begin() as conn:
            insert_note(conn, case_id=case_id, author_id=user_id, text=text_with_nul)
        with migrated_engine.connect() as conn:
            assert conn.execute(select(Note.__table__.c.text)).scalar_one() == text_with_nul
