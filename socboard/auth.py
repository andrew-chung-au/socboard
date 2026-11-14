"""Authentication: the signing secret, `current_user`, the role checks, login and the user list (#13).

Request flow: `HTTPBearer(auto_error=False)` reads the `Authorization` header and declares the
bearer scheme to FastAPI; `current_user` verifies the token, loads the user from the database and
raises 401 `unauthorized` itself (FastAPI's own helpers answer 403 for a missing credential);
`require_lead` and `ensure_lead` check the role. Authentication is always a route DEPENDENCY,
never an inline check in a handler, so the contract's order of checks for requests that are not
well-formed holds (`tests/test_backend_order.py`).

The role is read from the database on every request, never from the token, so a removed user or
a changed role takes effect at once. Every refusal is an `ApiHTTPException` with a contract code
and fixed text. Log lines hold fixed text and a numeric user id only: never a token, a password,
a hash or a submitted username.

The decision record is `_docs/decisions/2026-11-12T2255Z-auth-tokens.md`.
"""

import hashlib
import hmac
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from socboard import passwords, ratelimit, tokens
from socboard.errors import (
    FORBIDDEN,
    INVALID_API_KEY,
    INVALID_CREDENTIALS,
    RATE_LIMITED,
    UNAUTHORIZED,
    ApiError,
    ApiHTTPException,
)
from socboard.models import User as UserRow
from socboard.schemas import LoginRequest, LoginResponse, User
from socboard.sessions import get_session

logger = logging.getLogger("socboard.auth")

SECRET_ENV_NAME = "SOCBOARD_TOKEN_SECRET"
MIN_SECRET_LENGTH = 32
SECRET_MESSAGE = (
    f"{SECRET_ENV_NAME} must be set to at least {MIN_SECRET_LENGTH} characters, "
    "not counting leading and trailing spaces."
)

UNAUTHORIZED_MESSAGE = "Your session has expired. Please sign in again."
FORBIDDEN_MESSAGE = "You do not have permission to do this."
INVALID_CREDENTIALS_MESSAGE = "Invalid username or password."
INVALID_API_KEY_MESSAGE = "A valid API key is required."
RATE_LIMITED_MESSAGE = "Too many failed sign-in attempts. Try again later."
INGEST_RATE_LIMITED_MESSAGE = "Too many alerts. Try again later."
KEY_HEADER = "X-API-Key"

ROLE_LEAD = "lead"


class ConfigError(ValueError):
    """A required setting is missing or unusable. The message names the variable only."""


def resolve_token_secret(environ: Mapping[str, str] | None = None) -> str:
    """The signing secret from `SOCBOARD_TOKEN_SECRET`. There is no default."""
    source = os.environ if environ is None else environ
    secret = source.get(SECRET_ENV_NAME, "")
    if len(secret.strip()) < MIN_SECRET_LENGTH:
        raise ConfigError(SECRET_MESSAGE)
    return secret


@dataclass(frozen=True)
class LoginGuard:
    """What `POST /login` needs to refuse repeated failures (#178): the counters and the proxies
    whose `X-Forwarded-For` is believed. Built once in `create_app`."""

    limiter: ratelimit.LoginLimiter
    trusted_proxies: frozenset[str]


def resolve_login_guard(environ: Mapping[str, str] | None = None) -> LoginGuard:
    """The login limits from the environment, or `ConfigError` naming the variable."""
    source = os.environ if environ is None else environ
    try:
        limits = ratelimit.resolve_limits(source)
    except ratelimit.LimitConfigError as error:
        raise ConfigError(str(error)) from None
    return LoginGuard(ratelimit.LoginLimiter(limits), limits.trusted_proxies)


@dataclass(frozen=True)
class CurrentUser:
    """The caller, as read from the database for this request."""

    id: int
    role: str


def unauthorized() -> ApiHTTPException:
    return ApiHTTPException(
        401, UNAUTHORIZED, UNAUTHORIZED_MESSAGE, headers={"WWW-Authenticate": "Bearer"}
    )


def forbidden() -> ApiHTTPException:
    return ApiHTTPException(403, FORBIDDEN, FORBIDDEN_MESSAGE)


def invalid_credentials() -> ApiHTTPException:
    return ApiHTTPException(401, INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE)


def rate_limited(retry_after: int) -> ApiHTTPException:
    return ApiHTTPException(
        429, RATE_LIMITED, RATE_LIMITED_MESSAGE, headers={"Retry-After": str(retry_after)}
    )


def invalid_api_key() -> ApiHTTPException:
    return ApiHTTPException(401, INVALID_API_KEY, INVALID_API_KEY_MESSAGE)


bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name=KEY_HEADER, auto_error=False)


def _digest(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def require_api_key(
    request: Request, provided: Annotated[str | None, Depends(api_key_header)]
) -> None:
    """Return if the header holds the ingest key, else raise 401 `invalid_api_key` (#14).

    `api_key_header` gives None for a missing or empty header. Starlette decodes header bytes as
    Latin-1, so encoding back gives the bytes the client sent; the key is compared as UTF-8.
    """
    expected = request.app.state.ingest_api_key
    if expected is None or provided is None:
        raise invalid_api_key()
    sent = _digest(provided.encode("latin-1", "replace"))
    if not hmac.compare_digest(sent, _digest(expected.encode("utf-8"))):
        raise invalid_api_key()


def limit_ingest_rate(request: Request) -> None:
    """Route dependency after `require_api_key`: 429 `rate_limited` above the configured rate (#183).

    It runs only for a request with the valid key (a wrong or missing key was refused with 401
    first), and before the body is validated or anything is stored. It counts every such request,
    including one the body check later refuses with 422."""
    retry_after = request.app.state.ingest_limiter.admit()
    if retry_after is not None:
        logger.info("Ingest refused: rate limit")
        raise ApiHTTPException(
            429,
            RATE_LIMITED,
            INGEST_RATE_LIMITED_MESSAGE,
            headers={"Retry-After": str(retry_after)},
        )


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    """The caller's id and role, or 401 `unauthorized` with `WWW-Authenticate: Bearer`.

    `bearer_scheme` returns None for no header, an empty token and a scheme other than Bearer.
    """
    if credentials is None:
        raise unauthorized()
    user_id = tokens.read_token(
        credentials.credentials, request.app.state.token_secret, now=time.time()
    )
    if user_id is None:
        raise unauthorized()
    row = session.execute(
        select(UserRow.id, UserRow.role).where(UserRow.id == user_id)
    ).one_or_none()
    if row is None:
        raise unauthorized()
    return CurrentUser(id=row.id, role=row.role)


def ensure_lead(user: CurrentUser) -> None:
    """403 `forbidden` unless `user` is a Lead; for a handler that checks after its own lookup."""
    if user.role != ROLE_LEAD:
        raise forbidden()


def require_lead(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    """A route dependency: the caller if a Lead, else 403 `forbidden` (401 first, as above)."""
    ensure_lead(user)
    return user


def _find_user(session: Session, username: str) -> UserRow | None:
    # A NUL (Postgres refuses it in text) is no such user, before any lookup.
    if "\x00" in username:
        return None
    try:
        # A lone surrogate from a JSON escape cannot be bound by the driver: no such user.
        username.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return session.execute(select(UserRow).where(UserRow.username == username)).scalar_one_or_none()


def add_login_route(api_router: APIRouter) -> None:
    """`POST /login` on `api_router` (the router from `create_api_router`, which documents the
    contract's 422)."""

    @api_router.post(
        "/login",
        response_model=LoginResponse,
        responses={401: {"model": ApiError}, 429: {"model": ApiError}},
        tags=["auth"],
        operation_id="login",
    )
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        session: Annotated[Session, Depends(get_session)],
    ) -> LoginResponse:
        # Trimmed before lookup (contract), then matched exactly; the password is used as sent.
        username = body.username.strip()
        guard: LoginGuard = request.app.state.login_guard
        client = ratelimit.client_address(
            request.client.host if request.client else None,
            request.headers.getlist(ratelimit.FORWARDED_HEADER),
            guard.trusted_proxies,
        )
        # Before any lookup or hash work, and the same for a real and a made-up username.
        # The attempt is counted as a failure here, under the limiter's lock, so concurrent
        # attempts cannot all pass the check; a successful login takes the count back below.
        reservation = guard.limiter.reserve(client, username)
        if reservation.retry_after is not None:
            logger.info("Login refused: too many failures")
            raise rate_limited(reservation.retry_after)
        user = _find_user(session, username)
        if user is None:
            # The same work as a wrong password, so the two cannot be told apart by cost.
            passwords.verify_password(body.password, passwords.DUMMY_HASH)
            logger.info("Login refused")
            raise invalid_credentials()
        if not passwords.verify_password(body.password, user.password_hash):
            logger.info("Login refused")
            raise invalid_credentials()
        guard.limiter.release(client, username, reservation)
        token = tokens.create_token(user.id, request.app.state.token_secret, now=time.time())
        response.headers["Cache-Control"] = "no-store"
        logger.info("Login succeeded for user id %d", user.id)
        return LoginResponse(token=token, user=User.from_row(user))


def create_users_router(prefix: str) -> APIRouter:
    """`GET /users`: no input, so no 422 (it is not on the router that declares one)."""
    router = APIRouter(prefix=prefix, tags=["users"], responses={401: {"model": ApiError}})

    @router.get("/users", response_model=list[User], operation_id="listUsers")
    def list_users(
        user: Annotated[CurrentUser, Depends(current_user)],
        session: Annotated[Session, Depends(get_session)],
    ) -> list[User]:
        rows = session.execute(select(UserRow).order_by(UserRow.id)).scalars()
        return [User.from_row(row) for row in rows]

    return router
