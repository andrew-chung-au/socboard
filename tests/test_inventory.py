"""The asset lookup and the seed command (#12), on temporary migrated databases."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from socboard import database, domain, migrate, seed
from socboard.inventory import criticality_for_asset
from socboard.models import Asset
from data_support import (  # noqa: F401  (fixtures)
    insert_asset,
    isolated_database_env,
    migrated_engine,
    no_default_database_file,
    sqlite_url,
)


def test_lookup_is_exact_and_case_sensitive(migrated_engine):
    with migrated_engine.begin() as conn:
        insert_asset(conn, name="Host-A", criticality="Critical")
    with Session(migrated_engine) as session:
        assert criticality_for_asset(session, "Host-A") == "Critical"
        assert criticality_for_asset(session, "missing") is None
        assert criticality_for_asset(session, "host-a") is None
        assert criticality_for_asset(session, "Host-A ") is None
        assert criticality_for_asset(session, " Host-A") is None


def test_the_seed_data_meets_the_inventory_rules():
    assert len(seed.ASSETS) >= 8
    assert {level for _, level in seed.ASSETS} == set(domain.ASSET_CRITICALITIES)
    names = [name for name, _ in seed.ASSETS]
    assert len(set(names)) == len(names)
    assert all(0 < len(name) <= 200 for name in names)


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(Asset.__table__).order_by(Asset.__table__.c.id)).all()


def test_seed_loads_the_inventory_and_a_second_run_changes_nothing(tmp_path, capsys):
    url = sqlite_url(tmp_path / "seed.db")
    migrate.upgrade(url)
    env = {database.ENV_NAME: url}
    assert seed.main(env) == 0
    engine = database.create_db_engine(url)
    try:
        first = _rows(engine)
        assert {(r.name, r.criticality) for r in first} == set(seed.ASSETS)
        assert seed.main(env) == 0
        assert _rows(engine) == first
        with Session(engine) as session:
            assert criticality_for_asset(session, "dc-01") == "Critical"
    finally:
        engine.dispose()


def test_seed_never_changes_or_deletes_an_existing_row(tmp_path):
    url = sqlite_url(tmp_path / "seed.db")
    migrate.upgrade(url)
    engine = database.create_db_engine(url)
    try:
        with engine.begin() as conn:
            insert_asset(conn, name="dc-01", criticality="Low")
            insert_asset(conn, name="custom-asset", criticality="High")
        assert seed.main({database.ENV_NAME: url}) == 0
        with engine.connect() as conn:
            rows = {r.name: r.criticality for r in conn.execute(select(Asset.__table__))}
            count = conn.execute(select(func.count()).select_from(Asset.__table__)).scalar_one()
        assert rows["dc-01"] == "Low"
        assert rows["custom-asset"] == "High"
        assert count == len(seed.ASSETS) + 1
    finally:
        engine.dispose()


def test_seed_does_not_write_audit_entries(tmp_path):
    url = sqlite_url(tmp_path / "seed.db")
    migrate.upgrade(url)
    assert seed.main({database.ENV_NAME: url}) == 0
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM audit_entries")).scalar_one() == 0
    finally:
        engine.dispose()


def test_seed_on_an_unmigrated_database_exits_1_with_fixed_text(tmp_path, capsys):
    url = sqlite_url(tmp_path / "empty.db")
    assert seed.main({database.ENV_NAME: url}) == 1
    err = capsys.readouterr().err
    assert "make migrate" in err
    assert str(tmp_path) not in err


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url secret-password-1",
        "postgresql://user:secret-password-2@host:notaport/db",
        "unknowndialect://user:secret-password-3@host/db",
    ],
)
def test_seed_exits_2_for_a_bad_url_without_printing_it(raw, capsys):
    assert seed.main({database.ENV_NAME: raw}) == 2
    captured = capsys.readouterr()
    assert "secret-password" not in captured.out + captured.err
    if raw:
        assert raw not in captured.out + captured.err
    assert captured.err.strip()
