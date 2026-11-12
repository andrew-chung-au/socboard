"""The migrations (#11): run in code, in pytest, with no subprocess.

Alembic's configuration is built in code (`socboard.migrate.make_config`). Every database lives in
a pytest temporary directory.
"""

import ast
import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from data_support import (
    REPO_ROOT,
    db_url,  # noqa: F401
    insert_alert,
    insert_user,
    isolated_database_env,  # noqa: F401
    no_default_database_file,  # noqa: F401
    sqlite_url,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from socboard import database, migrate
from socboard._requirements import MissingPackageError
from socboard.models import Alert, metadata

VERSIONS = migrate.SCRIPT_LOCATION / "versions"


def table_rows(url):
    """The `sqlite_master` rows: type, name, table and SQL."""
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            ).all()
    finally:
        engine.dispose()
    return [tuple(row) for row in rows]


def model_differences(url):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            return compare_metadata(context, metadata)
    finally:
        engine.dispose()


def version_rows(url):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            return conn.exec_driver_sql("SELECT version_num FROM alembic_version").all()
    finally:
        engine.dispose()


# --- the real migrations ----------------------------------------------------------------------


def test_the_migrations_match_the_models(db_url):
    migrate.upgrade(db_url)
    assert model_differences(db_url) == []


def test_the_comparison_would_notice_a_difference(db_url):
    migrate.upgrade(db_url)
    engine = database.create_db_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN stray TEXT")
    finally:
        engine.dispose()
    assert model_differences(db_url) != []


def test_there_is_exactly_one_head():
    script = ScriptDirectory.from_config(migrate.make_config())
    assert len(script.get_heads()) == 1


def test_there_is_no_alembic_ini():
    assert not (REPO_ROOT / "alembic.ini").exists()


def test_migration_files_hold_literals_and_import_nothing_from_socboard():
    files = sorted(VERSIONS.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith("socboard"), (path.name, name)


def test_a_second_run_changes_nothing(db_url):
    migrate.upgrade(db_url)
    first = table_rows(db_url), version_rows(db_url)
    migrate.upgrade(db_url)
    second = table_rows(db_url), version_rows(db_url)
    assert first == second


def test_downgrade_to_base_and_upgrade_again_gives_identical_schema_rows(db_url):
    migrate.upgrade(db_url)
    before = table_rows(db_url)

    migrate.downgrade(db_url, "base")
    at_base = [row for row in table_rows(db_url) if not row[1].startswith("sqlite_")]
    assert [(row[0], row[1]) for row in at_base] == [("table", "alembic_version")]

    migrate.upgrade(db_url)
    assert table_rows(db_url) == before


def test_a_round_trip_leaves_empty_tables(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url, "base")
    migrate.upgrade(db_url)
    engine = database.create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(select(Alert.id)).all() == []
    finally:
        engine.dispose()


# --- the command: exit codes and fixed text ----------------------------------------------------


def test_main_applies_the_migrations_and_prints_no_url(tmp_path, capsys):
    url = sqlite_url(tmp_path / "main.db")
    assert migrate.main({database.ENV_NAME: url}) == 0
    captured = capsys.readouterr()
    assert str(tmp_path) not in captured.out + captured.err
    assert (tmp_path / "main.db").exists()
    assert model_differences(url) == []


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url secret-password-1",
        "postgresql://user:secret-password-2@host:notaport/db",
        "unknowndialect://user:secret-password-3@host/db",
    ],
)
def test_main_exits_2_for_a_bad_url_without_printing_it(raw, capsys):
    assert migrate.main({database.ENV_NAME: raw}) == 2
    captured = capsys.readouterr()
    assert "secret-password" not in captured.out + captured.err
    if raw:
        assert raw not in captured.out + captured.err
    assert captured.err.strip()  # fixed text, not silence


def test_main_exits_1_and_names_the_package_when_one_is_missing(monkeypatch, capsys):
    def missing():
        raise MissingPackageError("The data layer needs Alembic, which is not installed.")

    monkeypatch.setattr(migrate, "_load", missing)
    assert migrate.main({}) == 1
    assert "Alembic" in capsys.readouterr().err


def test_main_exits_1_when_the_migration_fails_and_prints_no_message(monkeypatch, tmp_path, capsys):
    def explode(url, revision="head", *, script_location=None):
        raise RuntimeError("secret-password-9")

    monkeypatch.setattr(migrate, "upgrade", explode)
    assert migrate.main({database.ENV_NAME: sqlite_url(tmp_path / "x.db")}) == 1
    captured = capsys.readouterr()
    assert "secret-password" not in captured.out + captured.err
    assert "RuntimeError" in captured.err


# --- temporary script folders -----------------------------------------------------------------

MINIMAL_ENV = '''
from alembic import context

config = context.config
context.configure(connection=config.attributes["connection"], target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
'''

PARENT_AND_CHILD = '''
import sqlalchemy as sa
from alembic import op

revision = "a1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "parent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )
    op.create_table(
        "child",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("parent.id"), nullable=False),
    )
    op.execute("INSERT INTO parent (id, name) VALUES (1, 'p')")
    op.execute("INSERT INTO child (id, parent_id) VALUES (1, 1)")


def downgrade():
    op.drop_table("child")
    op.drop_table("parent")
'''

REBUILD_PARENT = '''
import sqlalchemy as sa
from alembic import op

revision = "b2"
down_revision = "a1"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite's recipe for changing a table that others reference: new table, copy, drop, rename.
    op.create_table(
        "parent_new",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("extra", sa.String(), nullable=True),
    )
    op.execute("INSERT INTO parent_new (id, name) SELECT id, name FROM parent")
    op.drop_table("parent")
    op.rename_table("parent_new", "parent")


def downgrade():
    pass
'''

ORPHAN_CHILD = '''
import sqlalchemy as sa
from alembic import op

revision = "c3"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("parent", sa.Column("id", sa.Integer(), primary_key=True))
    op.create_table(
        "child",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("parent.id"), nullable=False),
    )
    op.execute("INSERT INTO child (id, parent_id) VALUES (1, 99)")


def downgrade():
    pass
'''


def make_script_dir(root: Path, name: str, migrations: dict[str, str]) -> Path:
    folder = root / name
    (folder / "versions").mkdir(parents=True)
    (folder / "env.py").write_text(MINIMAL_ENV, encoding="utf-8")
    shutil.copy(migrate.SCRIPT_LOCATION / "script.py.mako", folder / "script.py.mako")
    for filename, source in migrations.items():
        (folder / "versions" / filename).write_text(source, encoding="utf-8")
    return folder


# --- the foreign-key pragma and the migration connection (F9) ------------------------------------


def test_a_two_migration_example_that_rebuilds_a_referenced_table_applies(tmp_path):
    scripts = make_script_dir(
        tmp_path, "two", {"a1_parent_and_child.py": PARENT_AND_CHILD, "b2_rebuild.py": REBUILD_PARENT}
    )
    url = sqlite_url(tmp_path / "two.db")
    migrate.upgrade(url, script_location=scripts)

    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            children = conn.exec_driver_sql("SELECT id, parent_id FROM child").all()
            columns = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(parent)").all()]
            violations = conn.exec_driver_sql("PRAGMA foreign_key_check").all()
            parents = conn.exec_driver_sql("SELECT id, name FROM parent").all()
    finally:
        engine.dispose()
    assert [tuple(row) for row in children] == [(1, 1)]
    assert [tuple(row) for row in parents] == [(1, "p")]
    assert "extra" in columns
    assert violations == []


def test_the_same_two_migrations_fail_on_an_engine_with_foreign_keys_on(tmp_path):
    # The control: with the pragma on (the engine's setting for everything but the migration
    # connection) the DROP of a table with child rows is refused. It shows the example is real.
    scripts = make_script_dir(
        tmp_path, "control", {"a1_parent_and_child.py": PARENT_AND_CHILD, "b2_rebuild.py": REBUILD_PARENT}
    )
    engine = database.create_db_engine(sqlite_url(tmp_path / "control.db"))
    try:
        with engine.connect() as conn:
            with pytest.raises(IntegrityError):
                command.upgrade(migrate.make_config(scripts, conn), "head")
            conn.rollback()
    finally:
        engine.dispose()


def test_the_migration_connection_switches_foreign_keys_off_and_the_engine_does_not(tmp_path):
    seen = {}

    def observe(config):
        connection = config.attributes["connection"]
        seen["during"] = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()

    migrate.run(sqlite_url(tmp_path / "pragma.db"), observe)
    assert seen["during"] == 0

    engine = database.create_db_engine(sqlite_url(tmp_path / "pragma.db"))
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        engine.dispose()


def test_a_foreign_key_violation_left_by_a_migration_is_a_failure(tmp_path):
    scripts = make_script_dir(tmp_path, "orphan", {"c3_orphan.py": ORPHAN_CHILD})
    with pytest.raises(migrate.MigrationError):
        migrate.upgrade(sqlite_url(tmp_path / "orphan.db"), script_location=scripts)


def test_main_exits_1_for_a_foreign_key_violation(monkeypatch, tmp_path, capsys):
    scripts = make_script_dir(tmp_path, "orphan", {"c3_orphan.py": ORPHAN_CHILD})
    monkeypatch.setattr(migrate, "SCRIPT_LOCATION", scripts)
    assert migrate.main({database.ENV_NAME: sqlite_url(tmp_path / "orphan.db")}) == 1
    assert "foreign key" in capsys.readouterr().err


# --- generating a migration from the models (F8) ------------------------------------------------


def test_a_generated_migration_runs_unedited_and_imports_nothing_from_socboard(tmp_path):
    folder = tmp_path / "generated"
    (folder / "versions").mkdir(parents=True)
    shutil.copy(migrate.SCRIPT_LOCATION / "env.py", folder / "env.py")
    shutil.copy(migrate.SCRIPT_LOCATION / "script.py.mako", folder / "script.py.mako")

    empty_url = sqlite_url(tmp_path / "empty.db")
    engine = database.create_db_engine(empty_url)
    try:
        with engine.connect() as conn:
            command.revision(
                migrate.make_config(folder, conn),
                message="generated from the models",
                autogenerate=True,
                rev_id="gen1",
            )
    finally:
        engine.dispose()

    generated = list((folder / "versions").glob("*.py"))
    assert len(generated) == 1
    source = generated[0].read_text(encoding="utf-8")
    assert "sa.DateTime(timezone=True)" in source
    assert "UtcDateTime" not in source
    assert "import socboard" not in source
    assert "from socboard" not in source
    assert "socboard.db_types" not in source

    target_url = sqlite_url(tmp_path / "from-generated.db")
    migrate.upgrade(target_url, script_location=folder)
    assert model_differences(target_url) == []


def test_the_generated_migration_gives_a_database_the_application_can_use(tmp_path):
    folder = tmp_path / "generated"
    (folder / "versions").mkdir(parents=True)
    shutil.copy(migrate.SCRIPT_LOCATION / "env.py", folder / "env.py")
    shutil.copy(migrate.SCRIPT_LOCATION / "script.py.mako", folder / "script.py.mako")
    engine = database.create_db_engine(sqlite_url(tmp_path / "empty.db"))
    try:
        with engine.connect() as conn:
            command.revision(migrate.make_config(folder, conn), message="gen", autogenerate=True, rev_id="gen2")
    finally:
        engine.dispose()

    url = sqlite_url(tmp_path / "used.db")
    migrate.upgrade(url, script_location=folder)
    engine = database.create_db_engine(url)
    try:
        with engine.begin() as conn:
            insert_user(conn)
            insert_alert(conn)
    finally:
        engine.dispose()
