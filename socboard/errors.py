"""The one error body of the HTTP API, and the three handlers that produce it.

The shape is the contract's `ApiError` (`openapi.yaml`, #9): `{ code, message }`. The HTTP status
carries the status, so there is no `status` field. Messages are fixed text per code and never
contain a submitted value (contract, "Differences from the mock", item 5).

Three handlers produce every failure the app returns:

- `http_exception_handler`: Starlette's `HTTPException` (unknown route 404, wrong method 405 and
  any raised `HTTPException`). It passes `exc.headers` through, so a 405 keeps its `Allow` header.
- `validation_exception_handler`: `RequestValidationError`, always 422 `validation_error`. It never
  reads Pydantic's error list, because that list echoes the submitted `input`.
- `unexpected_exception_handler`: any other exception, 500 `internal_error`, generic message.
"""

import logging
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("socboard.errors")

# Codes from openapi.yaml (#9). Do not add a code here without a contract change.
UNAUTHORIZED = "unauthorized"
INVALID_API_KEY = "invalid_api_key"
INVALID_CREDENTIALS = "invalid_credentials"
FORBIDDEN = "forbidden"
NOT_FOUND = "not_found"
METHOD_NOT_ALLOWED = "method_not_allowed"
VALIDATION_ERROR = "validation_error"
INTERNAL_ERROR = "internal_error"

VALIDATION_MESSAGE = "The request is not valid."
INTERNAL_MESSAGE = "Something went wrong."

# Code and fixed message for a plain HTTPException, by status.
_BY_STATUS: dict[int, tuple[str, str]] = {
    401: (UNAUTHORIZED, "Your session has expired. Please sign in again."),
    403: (FORBIDDEN, "You do not have permission to do this."),
    404: (NOT_FOUND, "Not found."),
    405: (METHOD_NOT_ALLOWED, "Method not allowed."),
}


class ApiError(BaseModel):
    """The error body. The HTTP status carries the status, so there is no `status` field."""

    code: str
    message: str


class ApiHTTPException(StarletteHTTPException):
    """An HTTP failure that names its contract code and fixed message.

    Raise this for any refusal that needs a code other than the status default (for example a
    409 `already_claimed`). The message must be fixed text and must not contain submitted values.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=message, headers=dict(headers or {}))
        self.code = code
        self.message = message


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build a response whose body is exactly `{ code, message }`."""
    body = ApiError(code=code, message=message).model_dump()
    return JSONResponse(status_code=status_code, content=body, headers=dict(headers or {}))


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Unknown route, wrong method and raised HTTP exceptions, all as `{ code, message }`."""
    if isinstance(exc, ApiHTTPException):
        return error_response(exc.status_code, exc.code, exc.message, exc.headers)
    known = _BY_STATUS.get(exc.status_code)
    if known is None:
        # The contract has no code for this status: a route should raise ApiHTTPException.
        # Answer a generic 500 rather than invent a code, and log the status for the developer.
        logger.error("HTTPException with status %s has no contract code", exc.status_code)
        return error_response(500, INTERNAL_ERROR, INTERNAL_MESSAGE)
    code, message = known
    return error_response(exc.status_code, code, message, exc.headers)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Any schema failure is 422 `validation_error` with fixed text; `exc` is never read."""
    return error_response(422, VALIDATION_ERROR, VALIDATION_MESSAGE)


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Any other exception is 500 `internal_error`; the detail goes to the log, not the client."""
    logger.error("Unhandled exception", exc_info=exc)
    return error_response(500, INTERNAL_ERROR, INTERNAL_MESSAGE)


def register_error_handlers(app: FastAPI) -> None:
    """Install the three handlers, replacing FastAPI's defaults."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)
