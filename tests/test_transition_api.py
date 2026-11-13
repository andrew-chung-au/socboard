"""`POST /api/cases/{id}/transition` (`transitionCase`, #17): the workflow and its refusals.

Legal moves are New to Investigating and Investigating to Closed (spec section 4); closing needs a
reason. The order of checks is the contract's: 401, (schema 422), 404, 403, 409, 422.
"""

import itertools
import re
import threading
import time

import pytest
from backend_support import (
    ANALYST_ID,
    ANALYST_TOKEN,
    LEAD_ID,
    LEAD_TOKEN,
    TEST_TOKEN_SECRET,
    load_contract,
)
from fastapi.testclient import TestClient
from openapi_compare import canonical, compare_schemas, diff, join_server_base
from sqlalchemy import event, insert, select, update
from sqlalchemy.engine import Engine
from test_queue_api import add_case, run

from socboard import database
from socboard.models import AuditEntry, Case, Note, User
from socboard.tokens import create_token

ANALYST = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
LEAD = {"Authorization": f"Bearer {LEAD_TOKEN}"}
OTHER_ID = 3
OTHER = {"Authorization": f"Bearer {create_token(OTHER_ID, TEST_TOKEN_SECRET, now=time.time())}"}
STATUSES = ("New", "Investigating", "Closed")
LEGAL = {("New", "Investigating"), ("Investigating", "Closed")}
INVALID = {"code": "invalid_transition", "message": "That move is not allowed for this case."}
REASON_REQUIRED = {"code": "reason_required", "message": "A closing reason is required."}
FORBIDDEN_CODE = "forbidden"
NOT_FOUND = {"code": "not_found", "message": "Case not found."}


def move(client, case_id, to, reason=None, headers=ANALYST):
    body = {"to": to} if reason is None else {"to": to, "closingReason": reason}
    return client.post(f"/api/cases/{case_id}/transition", json=body, headers=headers)


def new_case(url, case_id=1, status="New", owner_id=ANALYST_ID):
    add_case(
        url,
        case_id,
        status=status,
        owner_id=owner_id,
        closing_reason="duplicate" if status == "Closed" else None,
    )


def add_other_analyst(url):
    run(
        url,
        lambda conn: conn.execute(
            insert(User.__table__).values(
                id=OTHER_ID,
                username="test-analyst-2",
                display_name="Other Analyst",
                role="analyst",
                password_hash="not-a-real-hash",
            )
        ),
    )


def state(url, case_id=1):
    """(status, closing_reason, owner_id) and the audit entries as (actor_id, action, detail)."""

    def work(conn):
        row = conn.execute(
            select(Case.status, Case.closing_reason, Case.owner_id).where(Case.id == case_id)
        ).one()
        audit = conn.execute(
            select(AuditEntry.actor_id, AuditEntry.action, AuditEntry.detail).where(
                AuditEntry.case_id == case_id
            )
        ).all()
        return tuple(row), [tuple(entry) for entry in audit]

    return run(url, work)


# --- every pair of statuses --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "to"), list(itertools.product(STATUSES, STATUSES)), ids=lambda s: s
)
def test_every_pair_of_statuses(real_client, app_database, current, to):
    new_case(app_database, status=current)
    before = state(app_database)
    response = move(real_client, 1, to, "true_positive" if to == "Closed" else None)
    if (current, to) in LEGAL:
        assert response.status_code == 200
        assert response.json()["status"] == to
        reason = "true_positive" if to == "Closed" else None
        assert response.json()["closingReason"] == reason
        detail = f"{current} -> {to}" + (" (true_positive)" if to == "Closed" else "")
        assert state(app_database) == (
            (to, reason, ANALYST_ID),
            [(ANALYST_ID, "status_changed", detail)],
        )
    else:
        assert (response.status_code, response.json()) == (409, INVALID)
        assert state(app_database) == before == (before[0], [])


def test_new_to_investigating_to_closed_end_to_end(real_client, app_database):
    new_case(app_database)
    assert move(real_client, 1, "Investigating").status_code == 200
    assert move(real_client, 1, "Closed", "false_positive").status_code == 200
    assert state(app_database) == (
        ("Closed", "false_positive", ANALYST_ID),
        [
            (ANALYST_ID, "status_changed", "New -> Investigating"),
            (ANALYST_ID, "status_changed", "Investigating -> Closed (false_positive)"),
        ],
    )


# --- closing reason ----------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["true_positive", "false_positive", "duplicate"])
def test_each_closing_reason_closes(real_client, app_database, reason):
    new_case(app_database, status="Investigating")
    response = move(real_client, 1, "Closed", reason)
    assert response.status_code == 200
    assert state(app_database)[0] == ("Closed", reason, ANALYST_ID)


def test_closing_without_a_reason_is_422_and_the_case_stays_open(real_client, app_database):
    new_case(app_database, status="Investigating")
    response = move(real_client, 1, "Closed")
    assert (response.status_code, response.json()) == (422, REASON_REQUIRED)
    assert state(app_database) == (("Investigating", None, ANALYST_ID), [])


@pytest.mark.parametrize("reason", ["fixed", "", None, 1, "TRUE_POSITIVE"])
def test_an_unknown_reason_is_a_schema_422_and_the_case_stays_open(
    real_client, app_database, reason
):
    new_case(app_database, status="Investigating")
    response = real_client.post(
        "/api/cases/1/transition", json={"to": "Closed", "closingReason": reason}, headers=ANALYST
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert state(app_database) == (("Investigating", None, ANALYST_ID), [])


def test_a_valid_reason_on_a_move_that_is_not_a_close_is_ignored(real_client, app_database):
    # Contract, "Stated facts" (see the #17 issue comment on the criterion that differs).
    new_case(app_database)
    response = move(real_client, 1, "Investigating", "duplicate")
    assert response.status_code == 200
    assert response.json()["closingReason"] is None
    assert state(app_database) == (
        ("Investigating", None, ANALYST_ID),
        [(ANALYST_ID, "status_changed", "New -> Investigating")],
    )


# --- who may move a case -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("owner_id", "headers", "allowed"),
    [
        (ANALYST_ID, ANALYST, True),  # owner
        (ANALYST_ID, OTHER, False),  # another Analyst
        (ANALYST_ID, LEAD, True),  # a Lead on someone else's case
        (None, LEAD, True),  # unowned, Lead
        (None, ANALYST, False),  # unowned, Analyst
    ],
    ids=["owner", "other-analyst", "lead", "unowned-lead", "unowned-analyst"],
)
def test_only_the_owner_or_a_lead_may_move(real_client, app_database, owner_id, headers, allowed):
    add_other_analyst(app_database)
    new_case(app_database, owner_id=owner_id)
    response = move(real_client, 1, "Investigating", headers=headers)
    if allowed:
        assert response.status_code == 200
        actor = LEAD_ID if headers is LEAD else ANALYST_ID
        assert state(app_database) == (
            ("Investigating", None, owner_id),
            [(actor, "status_changed", "New -> Investigating")],
        )
    else:
        assert response.status_code == 403
        assert response.json()["code"] == FORBIDDEN_CODE
        assert state(app_database) == (("New", None, owner_id), [])


# --- order of checks: 401, 404, 403, 409, 422 ---------------------------------------------------


def test_401_comes_first(real_client, app_database):
    response = move(real_client, 999, "Closed", headers={})
    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")


@pytest.mark.parametrize("case_id", ["999", "C-1", "0"])
def test_404_comes_before_403(real_client, app_database, case_id):
    add_other_analyst(app_database)
    response = move(real_client, case_id, "Closed", headers=OTHER)
    assert (response.status_code, response.json()) == (404, NOT_FOUND)


def test_403_comes_before_409(real_client, app_database):
    add_other_analyst(app_database)
    new_case(app_database, status="Closed")
    response = move(real_client, 1, "New", headers=OTHER)
    assert response.status_code == 403


def test_409_comes_before_reason_required(real_client, app_database):
    # Contract example: a close without a reason on a New case is 409, not 422.
    new_case(app_database)
    response = move(real_client, 1, "Closed")
    assert (response.status_code, response.json()) == (409, INVALID)
    assert state(app_database) == (("New", None, ANALYST_ID), [])


def test_403_comes_before_reason_required(real_client, app_database):
    add_other_analyst(app_database)
    new_case(app_database, status="Investigating")
    assert move(real_client, 1, "Closed", headers=OTHER).status_code == 403


# --- saved together, one conditional update ----------------------------------------------------


def test_the_status_and_the_audit_entry_are_saved_together(app, app_database, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr("socboard.cases.append_audit", fail)
    new_case(app_database, status="Investigating")
    response = move(TestClient(app, raise_server_exceptions=False), 1, "Closed", "duplicate")
    assert (response.status_code, response.json()["code"]) == (500, "internal_error")
    assert state(app_database) == (("Investigating", None, ANALYST_ID), [])


def test_the_response_is_built_before_the_commit(real_client, app_database, monkeypatch):
    from socboard import cases

    order = []
    real_detail = cases._case_detail

    def detail(session, case_id):
        order.append("detail")
        return real_detail(session, case_id)

    def commit(conn):
        order.append("commit")

    new_case(app_database)
    monkeypatch.setattr(cases, "_case_detail", detail)
    event.listen(Engine, "commit", commit)
    try:
        assert move(real_client, 1, "Investigating").status_code == 200
    finally:
        event.remove(Engine, "commit", commit)
    assert order == ["detail", "commit"]


def test_the_move_is_one_conditional_update_on_status(real_client, app_database):
    new_case(app_database)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if re.search(r"\bcases\b", statement):
            statements.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        assert move(real_client, 1, "Investigating").status_code == 200
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    first = statements[0]
    assert first.startswith("UPDATE cases SET status=")
    assert "cases.status =" in first and "cases.owner_id =" in first
    assert sum(s.startswith("UPDATE cases") for s in statements) == 1


def test_two_simultaneous_closes_have_exactly_one_winner(real_app, app_database):
    """Both closes wait behind a third connection that holds the case row's write lock, then
    race when it rolls back. Holding a row by updating it blocks other writers on SQLite and
    Postgres alike, so the test runs unchanged on Postgres (as #16's claim test)."""
    new_case(app_database, status="Investigating")
    callers = {"owner": (ANALYST, "true_positive"), "lead": (LEAD, "duplicate")}
    barrier = threading.Barrier(len(callers) + 1, timeout=10)  # a failing thread can't hang the run
    responses = {}

    def attempt(name):
        client = TestClient(real_app)  # each request gets its own session from get_session
        headers, reason = callers[name]
        barrier.wait()
        responses[name] = move(client, 1, "Closed", reason, headers=headers)

    threads = [threading.Thread(target=attempt, args=(name,)) for name in callers]
    blocker = database.create_db_engine(app_database)
    try:
        with blocker.connect() as conn:
            conn.execute(update(Case).where(Case.id == 1).values(status=Case.status))
            for thread in threads:
                thread.start()
            barrier.wait()
            time.sleep(0.5)  # well inside the 5 s busy timeout; both closes are now waiting
            conn.rollback()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        blocker.dispose()

    assert sorted(r.status_code for r in responses.values()) == [200, 409]
    (winner,) = [name for name, r in responses.items() if r.status_code == 200]
    (loser,) = [name for name, r in responses.items() if r.status_code == 409]
    assert responses[loser].json() == INVALID
    actor = {"owner": ANALYST_ID, "lead": LEAD_ID}[winner]
    reason = callers[winner][1]
    assert state(app_database) == (
        ("Closed", reason, ANALYST_ID),
        [(actor, "status_changed", f"Investigating -> Closed ({reason})")],
    )


def test_a_note_and_a_close_at_the_same_time_never_leave_a_note_after_the_close(
    real_app, app_database
):
    """Race 18-3: a note and a close wait behind a held row lock, then race (as the test above).
    The note either commits before the close or is refused 409; never a note after the close."""
    new_case(app_database, status="Investigating")
    barrier = threading.Barrier(3, timeout=10)  # a failing thread can't hang the run
    responses = {}

    def attempt(name):
        client = TestClient(real_app)
        barrier.wait()
        if name == "note":
            responses[name] = client.post(
                "/api/cases/1/notes", json={"text": "racing note"}, headers=LEAD
            )
        else:
            responses[name] = move(client, 1, "Closed", "duplicate")

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("note", "close")]
    blocker = database.create_db_engine(app_database)
    try:
        with blocker.connect() as conn:
            conn.execute(update(Case).where(Case.id == 1).values(status=Case.status))
            for thread in threads:
                thread.start()
            barrier.wait()
            time.sleep(0.5)  # well inside the 5 s busy timeout; both requests are now waiting
            conn.rollback()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        blocker.dispose()

    assert responses["close"].status_code == 200
    note = responses["note"]
    assert note.status_code in (201, 409)
    audit = run(
        app_database,
        lambda conn: conn.execute(
            select(AuditEntry.action).where(AuditEntry.case_id == 1).order_by(AuditEntry.id)
        ).scalars().all(),
    )
    notes = run(app_database, lambda conn: conn.execute(select(Note.id)).scalars().all())
    if note.status_code == 201:
        assert audit == ["note_added", "status_changed"]
        assert len(notes) == 1
    else:
        assert note.json()["code"] == "case_closed"
        assert audit == ["status_changed"]
        assert notes == []


@pytest.mark.parametrize(
    ("reason", "expected"),
    [(None, (422, "reason_required")), ("duplicate", (409, "invalid_transition"))],
)
def test_a_lost_close_that_sent_a_reason_is_409_not_reason_required(reason, expected):
    """17-2: the re-read after a 0-row UPDATE sees Investigating (another move landed in between,
    possible on Postgres only). A close that sent a reason lost a race; only a missing one is 422."""
    from socboard.auth import CurrentUser
    from socboard.cases import _transition_refusal

    analyst = CurrentUser(id=ANALYST_ID, role="analyst")
    refusal = _transition_refusal(
        ("Investigating", ANALYST_ID), analyst, "Closed", "Investigating", reason
    )
    assert (refusal.status_code, refusal.code) == expected


# --- queue and detail --------------------------------------------------------------------------


def test_a_closed_case_leaves_the_queue_and_the_detail_shows_the_reason(real_client, app_database):
    new_case(app_database, status="Investigating")
    new_case(app_database, case_id=2)
    assert move(real_client, 1, "Closed", "duplicate").status_code == 200
    queue = real_client.get("/api/queue", headers=ANALYST).json()["cases"]
    assert [entry["id"] for entry in queue] == ["2"]
    detail = real_client.get("/api/cases/1", headers=ANALYST).json()
    assert (detail["status"], detail["closingReason"]) == ("Closed", "duplicate")


# --- contract ----------------------------------------------------------------------------------


def test_the_transition_route_matches_the_contract(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    contract_op = join_server_base(contract)["/api/cases/{id}/transition"]["post"]
    generated_op = generated["paths"]["/api/cases/{id}/transition"]["post"]
    assert generated_op["operationId"] == contract_op["operationId"] == "transitionCase"
    assert set(generated_op["responses"]) == set(contract_op["responses"])

    def body(op):
        return op["requestBody"]["content"]["application/json"]["schema"]

    def schema(op, status):
        return op["responses"][status]["content"]["application/json"]["schema"]

    assert compare_schemas(contract, body(contract_op), generated, body(generated_op)) == []
    assert compare_schemas(contract, schema(contract_op, "200"), generated, schema(generated_op, "200")) == []
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    for status in ("401", "403", "404", "409", "422"):
        assert diff(api_error, canonical(generated, schema(generated_op, status))) == [], status
