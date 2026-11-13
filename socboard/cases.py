"""Read endpoints for cases: `GET /api/queue` and `GET /api/cases/{id}` (#15).

Both need a valid bearer token (`current_user`, a route dependency, so 401 comes before 404) and
write nothing: no commit, no audit entry. Alert text is returned as stored; escaping is the
frontend's job.

Queue order is a backend rule: severity by an explicit rank (Critical first), then the alert's own
timestamp oldest first, then case id ascending. The rank is a SQL CASE built from the constant
below, so SQLite and Postgres order alike and no text is formatted into a query.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case as sql_case
from sqlalchemy import select
from sqlalchemy.orm import Session

from socboard.auth import current_user
from socboard.domain import SEVERITIES, STATUS_CLOSED
from socboard.errors import NOT_FOUND, ApiError, ApiHTTPException
from socboard.ids import from_public_id, to_public_id
from socboard.models import Alert, Case, Note
from socboard.models import User as UserRow
from socboard.schemas import CaseDetail, CaseSummary, QueueResponse
from socboard.schemas import Note as NoteOut
from socboard.schemas import User
from socboard.sessions import get_session

# `SEVERITIES` runs Low to Critical; the rank is 0 for Critical.
SEVERITY_RANK = {name: rank for rank, name in enumerate(reversed(SEVERITIES))}

NOT_FOUND_MESSAGE = "Case not found."


def _summary_fields(case: Case, alert: Alert, owner: UserRow | None) -> dict:
    return {
        "id": to_public_id(case.id),
        "title": alert.title,
        "alertType": alert.alert_type,
        "source": alert.source,
        "asset": alert.asset,
        "assetCriticality": alert.asset_criticality,
        "severity": alert.severity,
        "status": case.status,
        "owner": None if owner is None else User.from_row(owner),
        "alertTimestamp": alert.alert_timestamp,
        "ingestedAt": alert.ingested_at,
        "closingReason": case.closing_reason,
    }


def _case_query():
    """Case, its alert and its owner (None when unclaimed)."""
    return (
        select(Case, Alert, UserRow)
        .join(Alert, Alert.id == Case.alert_id)
        .outerjoin(UserRow, UserRow.id == Case.owner_id)
    )


def create_cases_router(prefix: str) -> APIRouter:
    """The queue and the case read. Neither is on the router that documents a 422, because the
    queue takes no input; the case route declares it itself (a path id is input to FastAPI, but a
    string id cannot fail validation, so the 422 is documented and never returned)."""
    router = APIRouter(
        prefix=prefix,
        tags=["queue"],
        responses={401: {"model": ApiError}},
        dependencies=[Depends(current_user)],
    )

    @router.get("/queue", response_model=QueueResponse, operation_id="getQueue")
    def get_queue(session: Annotated[Session, Depends(get_session)]) -> QueueResponse:
        rows = session.execute(
            _case_query()
            .where(Case.status != STATUS_CLOSED)
            .order_by(
                sql_case(SEVERITY_RANK, value=Alert.severity), Alert.alert_timestamp, Case.id
            )
        ).all()
        cases = [CaseSummary(**_summary_fields(*row)) for row in rows]
        return QueueResponse(cases=cases, servedAt=datetime.now(UTC))

    @router.get(
        "/cases/{id}",
        response_model=CaseDetail,
        responses={404: {"model": ApiError}, 422: {"model": ApiError}},
        operation_id="getCase",
    )
    def get_case(id: str, session: Annotated[Session, Depends(get_session)]) -> CaseDetail:
        case_id = from_public_id(id)
        row = None if case_id is None else session.execute(
            _case_query().where(Case.id == case_id)
        ).one_or_none()
        if row is None:
            raise ApiHTTPException(404, NOT_FOUND, NOT_FOUND_MESSAGE)
        case, alert, owner = row
        notes = session.execute(
            select(Note, UserRow)
            .join(UserRow, UserRow.id == Note.author_id)
            .where(Note.case_id == case.id)
            .order_by(Note.created_at, Note.id)
        ).all()
        return CaseDetail(
            **_summary_fields(case, alert, owner),
            description=alert.description,
            severityRule=alert.severity_rule,
            notes=[
                NoteOut(
                    id=to_public_id(note.id),
                    author=User.from_row(author),
                    text=note.text,
                    createdAt=note.created_at,
                )
                for note, author in notes
            ],
        )

    return router
