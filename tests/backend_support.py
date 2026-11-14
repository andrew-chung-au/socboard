"""Test-only routes, test users and tokens for the backend tests (#10, #13).

`create_test_router()` adds routes that only the tests use (`/items`, `/boom`, `/raise-*`) to the
real API router, to exercise path and query validation and the error mapping. `GET /api/cases` is
the real route (`socboard/cases.py`); no test route serves it.

`ANALYST_TOKEN` and `LEAD_TOKEN` are real tokens, signed with `TEST_TOKEN_SECRET` for the two test
users that `build_test_database` inserts with fixed ids. `tests/conftest.py` sets the secret in the
environment and points the `app` fixture at a copy of that database. Every secret and password
here is synthetic and test-only.
"""

import time
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from sqlalchemy import insert

from socboard import database, migrate
from socboard.app import create_api_router
from socboard.errors import ApiHTTPException
from socboard.models import User
from socboard.passwords import hash_password
from socboard.tokens import create_token

REPO_ROOT = Path(__file__).resolve().parents[1]

# Synthetic, test-only values (at least 32 characters for the secret, 12 for a password).
TEST_TOKEN_SECRET = "test-only-signing-secret-0123456789abcdef"
ANALYST_ID = 1
LEAD_ID = 2
ANALYST_USERNAME = "test-analyst"
LEAD_USERNAME = "test-lead"
ANALYST_PASSWORD = "test-only-analyst-pw-1"
LEAD_PASSWORD = "test-only-lead-pw-2"

# Real tokens for the two test users; valid for 8 hours from import, far longer than a test run.
ANALYST_TOKEN = create_token(ANALYST_ID, TEST_TOKEN_SECRET, now=time.time())
LEAD_TOKEN = create_token(LEAD_ID, TEST_TOKEN_SECRET, now=time.time())
KNOWN_CASE_ID = "C-1001"
MARKER = "zz-marker-7f3a91"


def load_contract() -> dict:
    """Read openapi.yaml at test time (PyYAML comes with openapi-spec-validator)."""
    with (REPO_ROOT / "openapi.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def build_test_database(path: Path) -> str:
    """A migrated database at `path` holding the two test users; returns its URL."""
    url = sqlite_url(path)
    migrate.upgrade(url)
    engine = database.create_db_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(User.__table__),
                [
                    {
                        "id": ANALYST_ID,
                        "username": ANALYST_USERNAME,
                        "display_name": "Test Analyst",
                        "role": "analyst",
                        "password_hash": hash_password(ANALYST_PASSWORD),
                    },
                    {
                        "id": LEAD_ID,
                        "username": LEAD_USERNAME,
                        "display_name": "Test Lead",
                        "role": "lead",
                        "password_hash": hash_password(LEAD_PASSWORD),
                    },
                ],
            )
    finally:
        engine.dispose()
    return url


def create_test_router() -> APIRouter:
    router = create_api_router()

    @router.get("/items/{item_id}")
    def item_by_path(item_id: int) -> dict:
        return {"id": item_id}

    @router.get("/items")
    def items_by_query(limit: int) -> dict:
        return {"limit": limit}

    @router.get("/boom")
    def boom() -> dict:
        raise RuntimeError("secret-internal-detail /srv/app/secret_module.py")

    @router.get("/raise-http")
    def raise_http() -> dict:
        raise HTTPException(status_code=403, detail=f"detail-with-{MARKER}")

    @router.get("/raise-http-headers")
    def raise_http_headers() -> dict:
        raise HTTPException(status_code=404, detail="x", headers={"X-Test-Header": "kept"})

    @router.get("/raise-http-unmapped")
    def raise_http_unmapped() -> dict:
        raise HTTPException(status_code=418, detail="teapot")

    @router.get("/raise-api")
    def raise_api() -> dict:
        raise ApiHTTPException(409, "already_claimed", "Case is already owned.")

    return router
