"""Seeded users (#13): `python -m socboard.seed` adds two Analysts and one Lead.

Passwords come from three environment variables, with no default; a missing, empty or short one
fails the whole seed before anything is written.
"""

import pytest
from sqlalchemy import func, select, text

from socboard import database, migrate, passwords, seed
from socboard.models import Asset, User
from data_support import (  # noqa: F401  (fixtures)
    SEED_PASSWORDS,
    isolated_database_env,
    no_default_database_file,
    sqlite_url,
)

VARIABLES = (
    "SOCBOARD_SEED_PASSWORD_ANALYST_A",
    "SOCBOARD_SEED_PASSWORD_ANALYST_B",
    "SOCBOARD_SEED_PASSWORD_LEAD",
)


@pytest.fixture
def seed_url(tmp_path):
    url = sqlite_url(tmp_path / "seed.db")
    migrate.upgrade(url)
    return url


def _users(url):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(User.__table__).order_by(User.__table__.c.id)).all()
        return [row._asdict() for row in rows]
    finally:
        engine.dispose()


def _count(url, table):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(table)).scalar_one()
    finally:
        engine.dispose()


def test_the_seed_creates_the_three_mock_accounts(seed_url, capsys):
    assert seed.main({database.ENV_NAME: seed_url, **SEED_PASSWORDS}) == 0
    users = _users(seed_url)
    assert [(u["username"], u["display_name"], u["role"]) for u in users] == [
        ("analyst-a", "Ana Alvarez", "analyst"),
        ("analyst-b", "Ben Okafor", "analyst"),
        ("lead", "Lee Tanaka", "lead"),
    ]
    by_name = {u["username"]: u for u in users}
    for username, variable in zip(("analyst-a", "analyst-b", "lead"), VARIABLES):
        stored = by_name[username]["password_hash"]
        assert stored != SEED_PASSWORDS[variable]
        assert SEED_PASSWORDS[variable] not in stored
        assert stored.startswith("scrypt$")
        assert passwords.verify_password(SEED_PASSWORDS[variable], stored)
    out = capsys.readouterr()
    for value in SEED_PASSWORDS.values():
        assert value not in out.out + out.err
    for user in users:
        assert user["password_hash"] not in out.out + out.err


def test_the_stored_hash_carries_the_owasp_parameters(seed_url):
    assert seed.main({database.ENV_NAME: seed_url, **SEED_PASSWORDS}) == 0
    for user in _users(seed_url):
        n, r, p, salt, _ = passwords.parse_hash(user["password_hash"])
        assert (n, r, p) == (2**17, 8, 1)
        assert len(salt) >= 16


def test_two_users_with_the_same_password_get_different_hashes(seed_url):
    same = "test-only-shared-seed-pw"
    env = {database.ENV_NAME: seed_url, **{variable: same for variable in VARIABLES}}
    assert seed.main(env) == 0
    hashes = [user["password_hash"] for user in _users(seed_url)]
    assert len(set(hashes)) == 3
    assert all(passwords.verify_password(same, stored) for stored in hashes)


def test_a_second_run_creates_no_duplicate_and_changes_no_user(seed_url, capsys):
    env = {database.ENV_NAME: seed_url, **SEED_PASSWORDS}
    assert seed.main(env) == 0
    first = _users(seed_url)
    # Different passwords on the second run: an existing user keeps its hash (no reset).
    changed = {variable: value + "-changed" for variable, value in SEED_PASSWORDS.items()}
    assert seed.main({database.ENV_NAME: seed_url, **changed}) == 0
    assert _users(seed_url) == first
    assert "0 user(s) added, 3 already present" in capsys.readouterr().out


def test_an_existing_user_is_left_as_it_is(seed_url):
    engine = database.create_db_engine(seed_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                User.__table__.insert().values(
                    username="lead", display_name="Someone Else", role="analyst", password_hash="x"
                )
            )
    finally:
        engine.dispose()
    assert seed.main({database.ENV_NAME: seed_url, **SEED_PASSWORDS}) == 0
    lead = next(u for u in _users(seed_url) if u["username"] == "lead")
    assert (lead["display_name"], lead["role"], lead["password_hash"]) == ("Someone Else", "analyst", "x")
    assert len(_users(seed_url)) == 3


def _bad_values():
    return {"missing": None, "empty": "", "eleven characters": "x" * 11}


@pytest.mark.parametrize("variable", VARIABLES)
@pytest.mark.parametrize("kind", sorted(_bad_values()))
def test_a_bad_password_variable_fails_the_whole_seed(seed_url, capsys, variable, kind):
    env = {database.ENV_NAME: seed_url, **SEED_PASSWORDS}
    value = _bad_values()[kind]
    if value is None:
        del env[variable]
    else:
        env[variable] = value
    assert seed.main(env) != 0
    err = capsys.readouterr().err
    assert variable in err
    for other in VARIABLES:
        if other != variable:
            assert other not in err
    for good in SEED_PASSWORDS.values():
        assert good not in err
    if value:
        assert value not in err
    # All or nothing: no user, and no asset either.
    assert _count(seed_url, User.__table__) == 0
    assert _count(seed_url, Asset.__table__) == 0


def test_twelve_characters_is_enough(seed_url):
    env = {database.ENV_NAME: seed_url, **{variable: "y" * 12 for variable in VARIABLES}}
    assert seed.main(env) == 0
    assert _count(seed_url, User.__table__) == 3


def test_all_three_missing_names_all_three(seed_url, capsys):
    assert seed.main({database.ENV_NAME: seed_url}) != 0
    err = capsys.readouterr().err
    for variable in VARIABLES:
        assert variable in err
    assert _count(seed_url, User.__table__) == 0


def test_seeding_users_writes_no_audit_entry(seed_url):
    assert seed.main({database.ENV_NAME: seed_url, **SEED_PASSWORDS}) == 0
    engine = database.create_db_engine(seed_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM audit_entries")).scalar_one() == 0
    finally:
        engine.dispose()


def test_there_is_no_default_password(seed_url):
    # Each variable is read with no fallback: an environment without them seeds nothing.
    found, problems = seed.read_seed_passwords({})
    assert found == {}
    assert len(problems) == 3
