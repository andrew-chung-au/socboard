"""Fixtures for the backend tests.

The imports sit inside the fixtures so a missing backend package fails only the tests that need
it, with a plain ImportError naming the package, and the older tests still run.

Every test runs with `SOCBOARD_TOKEN_SECRET` set to a synthetic test-only value (#13) and with
`SOCBOARD_DATABASE_URL` pointing at a file in its own temporary directory, so no test can reach
the default `socboard.db`. The `app` fixture's database is a copy of one migrated database with
the two test users of `backend_support.py`, built once per run.
"""

import shutil

import pytest


@pytest.fixture(autouse=True)
def auth_test_environment(monkeypatch, tmp_path):
    """The signing secret for every test, and a database URL that is never the default file."""
    monkeypatch.setenv("SOCBOARD_TOKEN_SECRET", "test-only-signing-secret-0123456789abcdef")
    monkeypatch.setenv("SOCBOARD_DATABASE_URL", "sqlite:///" + (tmp_path / "unused.db").as_posix())


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

    path = tmp_path_factory.mktemp("template") / "template.db"
    build_test_database(path)
    return path


@pytest.fixture
def app_database(template_database, tmp_path_factory, monkeypatch):
    """A fresh copy of the template database; SOCBOARD_DATABASE_URL points at it.

    The copy lives outside the test's own `tmp_path`, which tests use to check that nothing
    created a database file there.
    """
    from backend_support import sqlite_url

    path = tmp_path_factory.mktemp("appdb") / "app.db"
    shutil.copyfile(template_database, path)
    url = sqlite_url(path)
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
