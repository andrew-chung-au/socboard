"""Fixtures for the backend tests.

The imports sit inside the fixtures so a missing backend package fails only the tests that need
it, with a plain ImportError naming the package, and the older tests still run.

Every test runs with `SOCBOARD_TOKEN_SECRET` set to a synthetic test-only value (#13) and with
`SOCBOARD_DATABASE_URL` pointing at a file in its own temporary directory, so no test can reach
the default `socboard.db`. The `app` fixture's database is a copy of one migrated database with
the two test users of `backend_support.py`, built once per run.
"""

import db_target
import pytest
import stack_support

MARKER_LINE = (
    "sqlite_only(reason): the test needs SQLite (a database file, SQLite triggers or pragmas); it is "
    "deselected when SOCBOARD_TEST_POSTGRES_URL is set (#192)"
)


POSTGRES_MARKER_LINE = (
    "postgres_only(reason): the test needs a Postgres server (its triggers, roles or catalogue); it is "
    "deselected when SOCBOARD_TEST_POSTGRES_URL is not set (#193)"
)


STACK_MARKER_LINE = (
    "stack_only(reason): the test needs a live Compose stack (SOCBOARD_BASE_URL set, `make test-stack`); it is "
    "deselected when the variable is unset (#229)"
)


def pytest_configure(config):
    config.addinivalue_line("markers", MARKER_LINE)
    config.addinivalue_line("markers", POSTGRES_MARKER_LINE)
    config.addinivalue_line("markers", STACK_MARKER_LINE)
    try:
        selected = db_target.server_url() is not None
    except ValueError as error:
        raise pytest.UsageError(str(error)) from None
    if selected:
        # Stop the run here, before any test, when the server cannot be reached. The error text is the
        # application's fixed connection text, which holds no part of the URL.
        try:
            import pg_roles

            config._postgres_databases_before = len(db_target.listed_databases())
            config._postgres_roles_before = len(pg_roles.listed_roles())
        except Exception as error:
            raise pytest.UsageError(f"cannot reach the Postgres server named by {db_target.ENV_NAME}: {error}") from None


def pytest_report_header(config):
    if not db_target.is_postgres():
        return "test database: SQLite (SOCBOARD_TEST_POSTGRES_URL is not set)"
    before = config._postgres_databases_before
    return f"test database: Postgres ({before} test database(s) on the server before the run)"


def _reason(marker) -> str:
    reason = marker.args[0] if marker.args else marker.kwargs.get("reason", "")
    return reason if isinstance(reason, str) else ""


def _marked(items, name):
    """The items that carry marker `name`; a marker without a one-line reason stops the run."""
    marked = []
    for item in items:
        for marker in item.iter_markers(name):
            reason = _reason(marker)
            if not reason.strip() or "\n" in reason.strip():
                raise pytest.UsageError(f"{item.nodeid}: {name} needs a one-line reason")
            marked.append(item)
            break
    return marked


def pytest_collection_modifyitems(config, items):
    """Check every database marker has a one-line reason; deselect the tests the run's database cannot run.

    `sqlite_only` tests are deselected on Postgres, `postgres_only` tests on SQLite. Deselected, never
    skipped (the verify gate refuses added skips), and the counts are printed.
    """
    sqlite_marked = _marked(items, "sqlite_only")
    postgres_marked = _marked(items, "postgres_only")
    postgres = db_target.is_postgres()
    drop = sqlite_marked if postgres else postgres_marked
    if drop:
        drop_ids = {id(item) for item in drop}
        items[:] = [item for item in items if id(item) not in drop_ids]
        config.hook.pytest_deselected(items=drop)
    config._sqlite_only_line = (
        f"sqlite_only: {len(sqlite_marked)} marked, {len(sqlite_marked) if postgres else 0} deselected on Postgres; "
        f"postgres_only: {len(postgres_marked)} marked, {0 if postgres else len(postgres_marked)} deselected on SQLite"
    )
    stack_marked = _marked(items, "stack_only")
    try:
        live = stack_support.is_live()
    except stack_support.StackSettingError as error:
        raise pytest.UsageError(str(error)) from None
    if stack_marked and not live:
        stack_ids = {id(item) for item in stack_marked}
        items[:] = [item for item in items if id(item) not in stack_ids]
        config.hook.pytest_deselected(items=stack_marked)
    config._stack_only_line = (
        f"stack_only: {len(stack_marked)} marked, {0 if live else len(stack_marked)} deselected "
        f"({'live stack' if live else 'SOCBOARD_BASE_URL is not set'})"
    )
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(config._sqlite_only_line)
        reporter.write_line(config._stack_only_line)


def pytest_terminal_summary(terminalreporter, config):
    terminalreporter.write_line(getattr(config, "_sqlite_only_line", "sqlite_only: not counted"))
    terminalreporter.write_line(getattr(config, "_stack_only_line", "stack_only: not counted"))
    if not db_target.is_postgres():
        return
    dropped = db_target.drop_databases(final=True)
    after = len(db_target.listed_databases())
    before = getattr(config, "_postgres_databases_before", 0)
    terminalreporter.write_line(
        f"postgres test databases: {before} before the run, {dropped} dropped at the end, {after} left on the server"
    )
    if after > before:
        terminalreporter.write_line("postgres test databases LEFT BEHIND by this run", red=True)
    import pg_roles

    roles_after = len(pg_roles.listed_roles())
    roles_before = getattr(config, "_postgres_roles_before", 0)
    terminalreporter.write_line(f"postgres test roles: {roles_before} before the run, {roles_after} left on the server")
    if roles_after > roles_before:
        terminalreporter.write_line("postgres test roles LEFT BEHIND by this run", red=True)


@pytest.fixture(autouse=True)
def drop_test_databases():
    """Drop the Postgres databases a test made, whether it passed or failed (a no-op on SQLite)."""
    start = db_target.created_count()
    yield
    if db_target.is_postgres():
        db_target.drop_databases(start)


@pytest.fixture(autouse=True)
def auth_test_environment(monkeypatch, tmp_path):
    """The signing secret for every test, and a database URL that is never the default file."""
    monkeypatch.setenv("SOCBOARD_TOKEN_SECRET", "test-only-signing-secret-0123456789abcdef")
    monkeypatch.setenv("SOCBOARD_DATABASE_URL", db_target.sqlite_url(tmp_path / "unused.db"))


@pytest.fixture(autouse=True)
def login_limits_raised(monkeypatch):
    """Raise the failed-login counts so no other test can trip them (#178).

    The limits come from configuration (README, "Login attempt limits"). `tests/test_login_limit.py`
    sets its own values where it needs the limits.
    """
    monkeypatch.setenv("SOCBOARD_LOGIN_MAX_FAILURES", "100000")
    monkeypatch.setenv("SOCBOARD_LOGIN_CLIENT_MAX_FAILURES", "100000")
    monkeypatch.setenv("SOCBOARD_INGEST_MAX_REQUESTS", "100000")


@pytest.fixture(autouse=True)
def no_built_frontend(monkeypatch, tmp_path):
    """Point the site folder at a missing one, so a built `frontend/dist` never reaches the tests (#140).

    `tests/test_spa_serving.py` sets the variable itself, which overrides this.
    """
    monkeypatch.setenv("SOCBOARD_STATIC_DIR", (tmp_path / "no-built-frontend").as_posix())


@pytest.fixture(scope="session")
def template_database(tmp_path_factory):
    """One migrated database with the two test users, copied for each test that needs it."""
    from backend_support import build_test_database

    url = db_target.new_template_url(tmp_path_factory.mktemp("template"))
    build_test_database(url)
    return url


@pytest.fixture
def app_database(template_database, tmp_path_factory, monkeypatch):
    """A fresh copy of the template database; SOCBOARD_DATABASE_URL points at it.

    On SQLite the copy is a file outside the test's own `tmp_path`, which tests use to check that
    nothing created a database file there. On Postgres it is a new database made from the template.
    """
    url = db_target.copy_of(template_database, tmp_path_factory.mktemp("appdb"))
    monkeypatch.setenv("SOCBOARD_DATABASE_URL", url)
    return url


@pytest.fixture
def real_app(app_database):
    """The app exactly as `create_app()` builds it, on the test database."""
    from socboard.app import create_app

    return create_app()


@pytest.fixture
def app(app_database):
    """The real app plus the test-only routes of `backend_support.py`.

    Those are `/items`, `/boom` and `/raise-*`.
    """
    from backend_support import create_test_router

    from socboard.app import create_app

    application = create_app()
    application.include_router(create_test_router())
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def real_client(real_app):
    from fastapi.testclient import TestClient

    return TestClient(real_app)


@pytest.fixture
def crash_client(app):
    """TestClient re-raises unhandled exceptions by default; this one returns the 500."""
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def real_crash_client(real_app):
    """Like `crash_client`, over the real app only: no test-only routes."""
    from fastapi.testclient import TestClient

    return TestClient(real_app, raise_server_exceptions=False)
