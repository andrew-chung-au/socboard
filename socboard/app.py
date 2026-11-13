"""The SOCBoard FastAPI application.

`create_app()` builds the app. Business routers (#14 onward) are created with
`create_api_router()` so every route that takes input documents the contract's 422 `ApiError`
and not FastAPI's own `HTTPValidationError`. The 422 is declared on that router, not on the app:
on the app, `/api/health` would gain a 422 as well.

Settings (#13), read when the app is built:
- `SOCBOARD_TOKEN_SECRET`: the token signing secret, at least 32 characters, no default. Without
  it `create_app()` raises `socboard.auth.ConfigError`, whose text names the variable only.
- `SOCBOARD_DATABASE_URL`: the database (#11). Only the URL is checked here; the engine is
  created on the first request that needs a session (`socboard.sessions`), so health needs no
  database file. Routes take a session through the `get_session` dependency and commit
  explicitly before they return.
- `SOCBOARD_INGEST_API_KEY`: the ingest API key (#14), at least 16 characters. Unlike the secret
  it is not required: without it `POST /api/alerts` is closed (401) and one warning is logged.
"""

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from socboard import __version__, database
from socboard.auth import add_login_route, create_users_router, resolve_token_secret
from socboard.errors import ApiError, register_error_handlers
from socboard.ingest import add_ingest_route, resolve_ingest_key
from socboard.sessions import DatabaseState

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


def create_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    source = os.environ if environ is None else environ
    token_secret = resolve_token_secret(source)
    ingest_api_key = resolve_ingest_key(source)
    database_state = DatabaseState(database.resolve_url(source))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        database_state.dispose()

    # No interactive docs and no served schema: the contract is openapi.yaml.
    app = FastAPI(
        title="SOCBoard API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.token_secret = token_secret
    app.state.ingest_api_key = ingest_api_key
    app.state.database = database_state
    register_error_handlers(app)
    app.include_router(create_health_router())
    auth_router = create_api_router()
    add_login_route(auth_router)
    app.include_router(auth_router)
    ingest_router = create_api_router()
    add_ingest_route(ingest_router)
    app.include_router(ingest_router)
    app.include_router(create_users_router(API_PREFIX))
    return app
