"""The SOCBoard FastAPI application.

`create_app()` builds the app. Business routers (#14 onward) are created with
`create_api_router()` so every route that takes input documents the contract's 422 `ApiError`
and not FastAPI's own `HTTPValidationError`. The 422 is declared on that router, not on the app:
on the app, `/api/health` would gain a 422 as well.
"""

from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from socboard import __version__
from socboard.errors import ApiError, register_error_handlers

API_PREFIX = "/api"


class HealthResponse(BaseModel):
    status: Literal["ok"]


def create_api_router() -> APIRouter:
    """A router under `/api` whose routes document 422 as the contract's `ApiError`."""
    return APIRouter(prefix=API_PREFIX, responses={422: {"model": ApiError}})


def create_health_router() -> APIRouter:
    """The health route: no authentication, no database, no input (so no 422)."""
    router = APIRouter(prefix=API_PREFIX, tags=["health"])

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return router


def create_app() -> FastAPI:
    # No interactive docs and no served schema: the contract is openapi.yaml.
    app = FastAPI(
        title="SOCBoard API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    register_error_handlers(app)
    app.include_router(create_health_router())
    return app
