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

Notes (#18): `POST /api/cases/{id}/notes` commits once, with its audit entry. It starts with a
no-op conditional UPDATE on the case, so a close cannot slip in before the note (#17). The two
reads above still write nothing.

Transitions (#17): `POST /api/cases/{id}/transition` is, like the claim, one conditional UPDATE
that checks the current status (and the owner, for an Analyst); only a refused move reads the case,
to choose the refusal in the contract's order.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case as sql_case
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from socboard.audit import append_audit
from socboard.auth import ROLE_LEAD, CurrentUser, current_user, forbidden, require_lead
from socboard.domain import ACTION_CLAIMED, SEVERITIES, STATUS_CLOSED
from socboard.errors import (
    ALREADY_CLAIMED,
    CASE_CLOSED,
    EMPTY_NOTE,
    INVALID_TRANSITION,
    NOT_FOUND,
    REASON_REQUIRED,
    ApiError,
    ApiHTTPException,
)
from socboard.ids import from_public_id, to_public_id
from socboard.models import Alert, AuditEntry, Case, Note
from socboard.models import User as UserRow
from socboard.schemas import (
    CaseDetail,
    CaseSummary,
    NoteRequest,
    QueueResponse,
    TransitionRequest,
)
from socboard.schemas import AuditEntry as AuditEntryOut
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

# Transitions (#17): each target status and the one status it may be reached from (spec section 4).
MOVES = {"Investigating": "New", STATUS_CLOSED: "Investigating"}
INVALID_TRANSITION_MESSAGE = "That move is not allowed for this case."
REASON_REQUIRED_MESSAGE = "A closing reason is required."


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
        # A no-op conditional UPDATE takes the case row's write lock (SQLite and Postgres) before
        # the check, so a close cannot commit between the check and the insert (as the claim).
        is_open = case_id is not None and session.execute(
            update(Case)
            .where(Case.id == case_id, Case.status != STATUS_CLOSED)
            .values(status=Case.status)
        ).rowcount == 1
        if not is_open:
            exists = case_id is not None and session.scalar(
                select(Case.id).where(Case.id == case_id)
            ) is not None
            session.rollback()
            if not exists:
                raise ApiHTTPException(404, NOT_FOUND, NOT_FOUND_MESSAGE)
            raise ApiHTTPException(409, CASE_CLOSED, NOTE_CASE_CLOSED_MESSAGE)
        if not body.text.strip():
            session.rollback()
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

    @router.post(
        "/cases/{id}/transition",
        response_model=CaseDetail,
        responses={
            403: {"model": ApiError},
            404: {"model": ApiError},
            409: {"model": ApiError},
            422: {"model": ApiError},
        },
        operation_id="transitionCase",
    )
    def transition_case(
        id: str,
        body: TransitionRequest,
        user: Annotated[CurrentUser, Depends(current_user)],
        session: Annotated[Session, Depends(get_session)],
    ) -> CaseDetail:
        case_id = from_public_id(id)
        closing = body.to == STATUS_CLOSED
        # A valid reason on a move that is not a close is ignored (contract, "Stated facts").
        reason = body.closingReason if closing else None
        source = MOVES.get(body.to)
        won = False
        if case_id is not None and source is not None and (reason is not None or not closing):
            conditions = [Case.id == case_id, Case.status == source]
            if user.role != ROLE_LEAD:
                conditions.append(Case.owner_id == user.id)
            # One conditional UPDATE: two simultaneous moves cannot both apply.
            won = session.execute(
                update(Case).where(*conditions).values(status=body.to, closing_reason=reason)
            ).rowcount == 1
        if not won:
            # Nothing was written; read the case only to choose the refusal, then roll back.
            row = None if case_id is None else session.execute(
                select(Case.status, Case.owner_id).where(Case.id == case_id)
            ).one_or_none()
            session.rollback()
            raise _transition_refusal(row, user, body.to, source, reason)
        detail_text = f"{source} -> {body.to}" + (f" ({reason})" if closing else "")
        append_audit(
            session,
            case_id=case_id,
            actor_id=user.id,
            action="status_changed",
            detail=detail_text,
        )
        # Read before commit, so a saved move never answers 500 because of the read.
        detail = _case_detail(session, case_id)
        session.commit()
        return detail

    # Audit trail (#19)
    # Read-only and Lead-only. `require_lead` is a route dependency, so 403 comes before 404 (an
    # Analyst asking about an unknown id gets 403). There is no write route: PUT, PATCH, POST and
    # DELETE to this path get the app's 405.
    @router.get(
        "/cases/{id}/audit",
        response_model=list[AuditEntryOut],
        dependencies=[Depends(require_lead)],
        responses={403: {"model": ApiError}, 404: {"model": ApiError}, 422: {"model": ApiError}},
        operation_id="getAudit",
    )
    def get_audit(
        id: str, session: Annotated[Session, Depends(get_session)]
    ) -> list[AuditEntryOut]:
        case_id = from_public_id(id)
        if case_id is None or session.scalar(select(Case.id).where(Case.id == case_id)) is None:
            raise _not_found()
        rows = session.execute(
            select(AuditEntry, UserRow)
            .outerjoin(UserRow, UserRow.id == AuditEntry.actor_id)
            .where(AuditEntry.case_id == case_id)
            .order_by(AuditEntry.at, AuditEntry.id)
        ).all()
        return [
            AuditEntryOut(
                id=to_public_id(entry.id),
                at=entry.at,
                actor="system" if actor is None else User.from_row(actor),
                action=entry.action,
                detail=entry.detail,
            )
            for entry, actor in rows
        ]

    return router


def _transition_refusal(
    row, user: CurrentUser, to: str, source: str | None, reason: str | None
) -> ApiHTTPException:
    """The refusal for a move that changed nothing, in the contract's order: 404, 403, 409, 422."""
    if row is None:
        return _not_found()
    status, owner_id = row
    if user.role != ROLE_LEAD and owner_id != user.id:
        return forbidden()
    # A close that sent a reason lost a race (the status changed after the UPDATE): 409, not 422.
    if status == source and to == STATUS_CLOSED and reason is None:
        return ApiHTTPException(422, REASON_REQUIRED, REASON_REQUIRED_MESSAGE)
    return ApiHTTPException(409, INVALID_TRANSITION, INVALID_TRANSITION_MESSAGE)


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
