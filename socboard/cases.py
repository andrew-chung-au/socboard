"""Case endpoints: `GET /api/queue` and `GET /api/cases/{id}` (#15), `POST /api/cases/{id}/claim` (#16).

All need a valid bearer token (`current_user`, a route dependency, so 401 comes before 404). The
two reads write nothing: no commit, no audit entry. Alert text is returned as stored; escaping is
the frontend's job.

A claim is one conditional UPDATE (data-model decision record). A SQLite lock timeout while
waiting for another writer is not mapped: it reaches the generic 500 `internal_error` and the
session closes without committing, since the contract has no code for it.

Queue order is a backend rule: severity by an explicit rank (Critical first), then the alert's own
timestamp oldest first, then case id ascending. The rank is a SQL CASE built from the constant
below, so SQLite and Postgres order alike and no text is formatted into a query.

Notes (#18): `POST /api/cases/{id}/notes` is the one write here; it commits once, with its audit
entry. The two reads above still write nothing.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case as sql_case
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from socboard.audit import append_audit
from socboard.auth import CurrentUser, current_user
from socboard.domain import ACTION_CLAIMED, SEVERITIES, STATUS_CLOSED
from socboard.errors import (
    ALREADY_CLAIMED,
    CASE_CLOSED,
    EMPTY_NOTE,
    NOT_FOUND,
    ApiError,
    ApiHTTPException,
)
from socboard.ids import from_public_id, to_public_id
from socboard.models import Alert, Case, Note
from socboard.models import User as UserRow
from socboard.schemas import CaseDetail, CaseSummary, NoteRequest, QueueResponse
from socboard.schemas import Note as NoteOut
from socboard.schemas import User
from socboard.sessions import get_session

# `SEVERITIES` runs Low to Critical; the rank is 0 for Critical.
SEVERITY_RANK = {name: rank for rank, name in enumerate(reversed(SEVERITIES))}

NOT_FOUND_MESSAGE = "Case not found."
CASE_CLOSED_MESSAGE = "Closed cases cannot be claimed."
ALREADY_CLAIMED_MESSAGE = "Case is already owned."

# Notes (#18)
NOTE_CASE_CLOSED_MESSAGE = "Notes cannot be added to a closed case."
EMPTY_NOTE_MESSAGE = "Note text cannot be empty."


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
    """The queue, the case read and the claim. None is on the router that documents a 422, because
    the queue takes no input; the case read and the claim declare it themselves (a path id is input
    to FastAPI, but a string id cannot fail validation, so the 422 is documented and never
    returned)."""
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
        return _case_detail(session, from_public_id(id))

    @router.post(
        "/cases/{id}/claim",
        response_model=CaseDetail,
        responses={404: {"model": ApiError}, 409: {"model": ApiError}, 422: {"model": ApiError}},
        operation_id="claimCase",
    )
    def claim_case(
        id: str,
        user: Annotated[CurrentUser, Depends(current_user)],
        session: Annotated[Session, Depends(get_session)],
    ) -> CaseDetail:
        case_id = from_public_id(id)
        if case_id is None:
            raise _not_found()
        # The one conditional UPDATE of the data-model decision record: never read, then write.
        won = session.execute(
            update(Case)
            .where(Case.id == case_id, Case.owner_id.is_(None), Case.status != STATUS_CLOSED)
            .values(owner_id=user.id)
        ).rowcount == 1
        if not won:
            # Only a lost claim reads the case, to say why; nothing was written, so roll back.
            status = session.scalar(select(Case.status).where(Case.id == case_id))
            session.rollback()
            if status is None:
                raise _not_found()
            if status == STATUS_CLOSED:
                raise ApiHTTPException(409, CASE_CLOSED, CASE_CLOSED_MESSAGE)
            raise ApiHTTPException(409, ALREADY_CLAIMED, ALREADY_CLAIMED_MESSAGE)
        append_audit(
            session, case_id=case_id, actor_id=user.id, action=ACTION_CLAIMED, detail="Case claimed."
        )
        # Read before commit, so a saved claim never answers 500 because of the read.
        detail = _case_detail(session, case_id)
        session.commit()
        return detail

    # Notes (#18)
    @router.post(
        "/cases/{id}/notes",
        status_code=201,
        response_model=NoteOut,
        responses={404: {"model": ApiError}, 409: {"model": ApiError}, 422: {"model": ApiError}},
        operation_id="addNote",
    )
    def add_note(
        id: str,
        body: NoteRequest,
        user: Annotated[CurrentUser, Depends(current_user)],
        session: Annotated[Session, Depends(get_session)],
    ) -> NoteOut:
        # Order (contract): 401 (dependency), schema 422 (body), 404, 409, empty_note 422.
        case_id = from_public_id(id)
        status = None if case_id is None else session.execute(
            select(Case.status).where(Case.id == case_id)
        ).scalar_one_or_none()
        if status is None:
            raise ApiHTTPException(404, NOT_FOUND, NOT_FOUND_MESSAGE)
        if status == STATUS_CLOSED:
            raise ApiHTTPException(409, CASE_CLOSED, NOTE_CASE_CLOSED_MESSAGE)
        if not body.text.strip():
            raise ApiHTTPException(422, EMPTY_NOTE, EMPTY_NOTE_MESSAGE)
        author = session.execute(select(UserRow).where(UserRow.id == user.id)).scalar_one()
        try:
            note = Note(
                case_id=case_id,
                author_id=user.id,
                text=body.text,  # as sent, spaces kept
                created_at=datetime.now(UTC),
            )
            session.add(note)
            session.flush()
            # The detail names the note, never its text.
            append_audit(
                session,
                case_id=case_id,
                actor_id=user.id,
                action="note_added",
                detail=f"Note {to_public_id(note.id)} added",
            )
            # Built before the commit, which expires the rows.
            response = NoteOut(
                id=to_public_id(note.id),
                author=User.from_row(author),
                text=note.text,
                createdAt=note.created_at,
            )
            session.commit()
        except SQLAlchemyError:
            session.rollback()  # any refused flush or commit leaves nothing; the error is not mapped
            raise
        return response

    return router


def _not_found() -> ApiHTTPException:
    return ApiHTTPException(404, NOT_FOUND, NOT_FOUND_MESSAGE)


def _case_detail(session: Session, case_id: int | None) -> CaseDetail:
    """The case with its alert, owner and notes, or 404 `not_found`."""
    row = None if case_id is None else session.execute(
        _case_query().where(Case.id == case_id)
    ).one_or_none()
    if row is None:
        raise _not_found()
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
