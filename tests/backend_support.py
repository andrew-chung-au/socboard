"""Test-only routes and dependencies for the backend tests (#10).

Authentication and the role check are route DEPENDENCIES here, never inline in a handler: the
order of checks in the contract (401, then a case-free role check, then the schema 422) holds only
that way. #13 and #14 replace these dependencies with the real ones; the order tests that use them
stay.

The dependencies read the `Authorization` header and raise 401 `unauthorized` or 403 `forbidden`
themselves (FastAPI's own security helpers answer 403 for a missing credential in some versions).
"""

from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from socboard.app import create_api_router
from socboard.errors import FORBIDDEN, NOT_FOUND, UNAUTHORIZED, ApiHTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]

ANALYST_TOKEN = "test-analyst-token"
LEAD_TOKEN = "test-lead-token"
KNOWN_CASE_ID = "C-1001"
MARKER = "zz-marker-7f3a91"

_ROLES = {ANALYST_TOKEN: "analyst", LEAD_TOKEN: "lead"}


def load_contract() -> dict:
    """Read openapi.yaml at test time (PyYAML comes with openapi-spec-validator)."""
    with (REPO_ROOT / "openapi.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def authenticate(authorization: Annotated[str | None, Header()] = None) -> str:
    """Return the caller's role, or raise 401 `unauthorized`."""
    prefix = "Bearer "
    if authorization is not None and authorization.startswith(prefix):
        role = _ROLES.get(authorization[len(prefix) :])
        if role is not None:
            return role
    raise ApiHTTPException(
        401,
        UNAUTHORIZED,
        "Your session has expired. Please sign in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_lead(role: Annotated[str, Depends(authenticate)]) -> str:
    """A role check that needs no case lookup: 403 `forbidden` for anyone but a Lead."""
    if role != "lead":
        raise ApiHTTPException(403, FORBIDDEN, "You do not have permission to do this.")
    return role


class TransitionBody(BaseModel):
    to: Literal["New", "Investigating", "Closed"]
    closingReason: Literal["true_positive", "false_positive", "duplicate"] | None = None


def create_test_router() -> APIRouter:
    router = create_api_router()

    @router.get("/cases")
    def closed_cases(
        status: Literal["Closed"],
        role: Annotated[str, Depends(require_lead)],
    ) -> list[dict]:
        return []

    @router.post("/cases/{id}/transition")
    def transition(
        id: str,
        body: TransitionBody,
        role: Annotated[str, Depends(authenticate)],
    ) -> dict:
        if id != KNOWN_CASE_ID:
            raise ApiHTTPException(404, NOT_FOUND, "Case not found.")
        return {"id": id, "status": body.to}

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
