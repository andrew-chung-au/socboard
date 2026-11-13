"""Ingest: `POST /api/alerts` with the shared API key (#14).

A source system is not a user. It sends the key in the `X-API-Key` header; a user's bearer token
does not work here and the key does not work anywhere else. The key comes from
`SOCBOARD_INGEST_API_KEY`, read when the app is built (`resolve_ingest_key`). Unset, empty or
shorter than 16 characters, the endpoint is closed: every request gets the same 401
`invalid_api_key` as a wrong key, and one warning is logged at start-up.

`require_api_key` is a route DEPENDENCY that raises the 401 itself, so the contract's order holds:
401 before the schema 422 (`tests/test_ingest_api.py`). `APIKeyHeader(auto_error=False)` only reads
the header and puts the scheme in the generated document. Both sides of the comparison are SHA-256
digests, so any length and any characters compare safely. The key is never logged, returned or
audited.

The route writes the alert, the case and the audit entry in one transaction and commits once.
The decision record is `_docs/decisions/2026-11-13T0158Z-ingest-api-key.md`.
"""

import hashlib
import hmac
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from socboard import scoring
from socboard.audit import append_audit
from socboard.domain import ACTION_INGESTED, STATUS_NEW
from socboard.errors import INVALID_API_KEY, ApiError, ApiHTTPException
from socboard.ids import to_public_id
from socboard.inventory import criticality_for_asset
from socboard.models import Alert, Case
from socboard.schemas import CaseSummary, IngestRequest
from socboard.sessions import get_session

logger = logging.getLogger("socboard.ingest")

KEY_ENV_NAME = "SOCBOARD_INGEST_API_KEY"
MIN_KEY_LENGTH = 16
KEY_HEADER = "X-API-Key"
INVALID_API_KEY_MESSAGE = "A valid API key is required."


def resolve_ingest_key(environ: Mapping[str, str] | None = None) -> str | None:
    """The key from `SOCBOARD_INGEST_API_KEY`, or None (ingest closed) with one warning."""
    source = os.environ if environ is None else environ
    key = source.get(KEY_ENV_NAME, "")
    if len(key) < MIN_KEY_LENGTH:
        logger.warning(
            "%s is unset, empty or shorter than %d characters: ingest is closed.",
            KEY_ENV_NAME,
            MIN_KEY_LENGTH,
        )
        return None
    return key


def invalid_api_key() -> ApiHTTPException:
    return ApiHTTPException(401, INVALID_API_KEY, INVALID_API_KEY_MESSAGE)


api_key_header = APIKeyHeader(name=KEY_HEADER, auto_error=False)


def _digest(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def require_api_key(
    request: Request, provided: Annotated[str | None, Depends(api_key_header)]
) -> None:
    """Return if the header holds the key, else raise 401 `invalid_api_key`.

    `api_key_header` gives None for a missing or empty header. Starlette decodes header bytes as
    Latin-1, so encoding back gives the bytes the client sent; the key is compared as UTF-8.
    """
    expected = request.app.state.ingest_api_key
    if expected is None or provided is None:
        raise invalid_api_key()
    sent = _digest(provided.encode("latin-1", "replace"))
    if not hmac.compare_digest(sent, _digest(expected.encode("utf-8"))):
        raise invalid_api_key()


def add_ingest_route(api_router: APIRouter) -> None:
    """`POST /alerts` on `api_router` (the router from `create_api_router`, which documents the
    contract's 422)."""

    @api_router.post(
        "/alerts",
        status_code=201,
        response_model=CaseSummary,
        responses={401: {"model": ApiError}},
        tags=["ingest"],
        operation_id="ingestAlert",
        dependencies=[Depends(require_api_key)],
    )
    def ingest_alert(
        body: IngestRequest, session: Annotated[Session, Depends(get_session)]
    ) -> CaseSummary:
        found = criticality_for_asset(session, body.asset)
        criticality = scoring.DEFAULT_CRITICALITY if found is None else found
        result = scoring.score(body.type, found)
        alert = Alert(
            source=body.source,
            alert_type=body.type,
            asset=body.asset,
            asset_criticality=criticality,
            title=body.title,
            description=body.description,
            alert_timestamp=body.timestamp,
            ingested_at=datetime.now(UTC),
            severity=result.severity,
            severity_rule=result.rule,
        )
        session.add(alert)
        session.flush()
        case = Case(alert_id=alert.id, status=STATUS_NEW)
        session.add(case)
        session.flush()
        append_audit(
            session,
            case_id=case.id,
            actor_id=None,
            action=ACTION_INGESTED,
            detail=f"Alert ingested from {body.source}; severity {result.severity}",
        )
        # Built before the commit, which expires the rows; one commit ends the transaction.
        response = CaseSummary(
            id=to_public_id(case.id),
            title=alert.title,
            alertType=alert.alert_type,
            source=alert.source,
            asset=alert.asset,
            assetCriticality=alert.asset_criticality,
            severity=alert.severity,
            status=case.status,
            owner=None,
            alertTimestamp=alert.alert_timestamp,
            ingestedAt=alert.ingested_at,
            closingReason=None,
        )
        session.commit()
        return response
