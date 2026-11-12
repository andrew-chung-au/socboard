"""Fixtures for the backend tests.

The imports sit inside the fixtures so a missing backend package fails only the tests that need
it, with a plain ImportError naming the package, and the older tests still run.
"""

import pytest


@pytest.fixture
def app():
    """The real app plus test-only routes (the business routes do not exist yet)."""
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
def crash_client(app):
    """TestClient re-raises unhandled exceptions by default; this one returns the 500."""
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)
