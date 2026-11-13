"""`POST /api/cases/{id}/claim` (`claimCase`, #16): one owner at a time.

Cases are written straight into the migrated test database with `add_case` from the queue tests.
There is no assignment table (data-model decision record): the owner is `cases.owner_id`, and the
assignment history is the `claimed` audit entry.
"""

import re
import threading
import time

import pytest
from backend_support import ANALYST_ID, ANALYST_TOKEN, LEAD_ID, LEAD_TOKEN, load_contract
from fastapi.testclient import TestClient
from openapi_compare import canonical, compare_schemas, diff, join_server_base
from sqlalchemy import event, select, update
from sqlalchemy.engine import Engine
from test_queue_api import add_case, run

from socboard import database
from socboard.models import AuditEntry, Case

ANALYST = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
LEAD = {"Authorization": f"Bearer {LEAD_TOKEN}"}
ALREADY_CLAIMED = {"code": "already_claimed", "message": "Case is already owned."}
CASE_CLOSED = {"code": "case_closed", "message": "Closed cases cannot be claimed."}
NOT_FOUND = {"code": "not_found", "message": "Case not found."}


def claim(client, case_id, headers=ANALYST):
    return client.post(f"/api/cases/{case_id}/claim", headers=headers)


def state(url, case_id):
    """(owner_id, status) of the case and its claimed audit entries as (actor_id, action)."""

    def work(conn):
        owner, status = conn.execute(
            select(Case.owner_id, Case.status).where(Case.id == case_id)
        ).one()
        audit = conn.execute(
            select(AuditEntry.actor_id, AuditEntry.action).where(AuditEntry.case_id == case_id)
        ).all()
        return owner, status, [tuple(row) for row in audit]

    return run(url, work)


# --- success -----------------------------------------------------------------------------------


def test_an_analyst_claims_an_unowned_case_and_only_the_owner_changes(real_client, app_database):
    add_case(app_database, 1, status="New")
    response = claim(real_client, 1)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "1"
    assert body["owner"]["id"] == str(ANALYST_ID)
    assert body["status"] == "New"
    assert {"description", "severityRule", "notes"} <= set(body)
    assert state(app_database, 1) == (ANALYST_ID, "New", [(ANALYST_ID, "claimed")])


def test_an_investigating_unowned_case_can_be_claimed_and_keeps_its_status(real_client, app_database):
    add_case(app_database, 1, status="Investigating")
    assert claim(real_client, 1).status_code == 200
    assert state(app_database, 1) == (ANALYST_ID, "Investigating", [(ANALYST_ID, "claimed")])


def test_a_lead_claims_a_case_like_an_analyst(real_client, app_database):
    add_case(app_database, 1)
    response = claim(real_client, 1, headers=LEAD)
    assert response.status_code == 200
    assert response.json()["owner"]["role"] == "lead"
    assert state(app_database, 1) == (LEAD_ID, "New", [(LEAD_ID, "claimed")])


def test_the_queue_and_the_detail_show_the_new_owner_on_the_next_read(real_client, app_database):
    add_case(app_database, 1)
    add_case(app_database, 2)
    assert claim(real_client, 1, headers=LEAD).status_code == 200
    queue = real_client.get("/api/queue", headers=ANALYST).json()["cases"]
    owners = {entry["id"]: entry["owner"] for entry in queue}
    assert owners["1"]["id"] == str(LEAD_ID) and owners["2"] is None
    detail = real_client.get("/api/cases/1", headers=ANALYST).json()
    assert detail["owner"]["id"] == str(LEAD_ID)


def test_the_owner_and_the_claimed_entry_are_saved_together(real_app, app_database, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr("socboard.cases.append_audit", fail)
    add_case(app_database, 1)
    response = claim(TestClient(real_app, raise_server_exceptions=False), 1)
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert state(app_database, 1) == (None, "New", [])


# --- refusals ----------------------------------------------------------------------------------


@pytest.mark.parametrize("headers", [ANALYST, LEAD], ids=["other-user", "current-owner"])
def test_a_claim_of_an_owned_case_is_409_and_writes_nothing(real_client, app_database, headers):
    add_case(app_database, 1, owner_id=LEAD_ID)
    response = claim(real_client, 1, headers=headers)
    assert (response.status_code, response.json()) == (409, ALREADY_CLAIMED)
    assert state(app_database, 1) == (LEAD_ID, "New", [])


def test_a_second_claim_by_the_winner_is_409(real_client, app_database):
    add_case(app_database, 1)
    assert claim(real_client, 1).status_code == 200
    response = claim(real_client, 1)
    assert (response.status_code, response.json()) == (409, ALREADY_CLAIMED)
    assert state(app_database, 1) == (ANALYST_ID, "New", [(ANALYST_ID, "claimed")])


@pytest.mark.parametrize("owner_id", [None, LEAD_ID], ids=["unowned", "owned"])
def test_a_claim_of_a_closed_case_is_409_case_closed_and_writes_nothing(
    real_client, app_database, owner_id
):
    add_case(app_database, 1, status="Closed", closing_reason="duplicate", owner_id=owner_id)
    response = claim(real_client, 1)
    assert (response.status_code, response.json()) == (409, CASE_CLOSED)
    assert state(app_database, 1) == (owner_id, "Closed", [])


@pytest.mark.parametrize("case_id", ["999", "C-1", "0"])
def test_an_unknown_id_is_404(real_client, app_database, case_id):
    add_case(app_database, 1)
    response = claim(real_client, case_id)
    assert (response.status_code, response.json()) == (404, NOT_FOUND)
    assert state(app_database, 1) == (None, "New", [])


@pytest.mark.parametrize("case_id", ["1", "999"])
def test_a_missing_token_is_401_before_404(real_client, app_database, case_id):
    add_case(app_database, 1)
    response = claim(real_client, case_id, headers={})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert state(app_database, 1) == (None, "New", [])


# --- one conditional update --------------------------------------------------------------------


def test_the_claim_is_one_conditional_update_with_no_read_of_the_case_first(
    real_client, app_database
):
    add_case(app_database, 1)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        if re.search(r"\bcases\b", statement):
            statements.append(" ".join(statement.split()))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        assert claim(real_client, 1).status_code == 200
        won = list(statements)
        statements.clear()
        assert claim(real_client, 1, headers=LEAD).status_code == 409
        lost = list(statements)
    finally:
        event.remove(Engine, "before_cursor_execute", record)

    for recorded in (won, lost):
        first = recorded[0]
        assert first.startswith("UPDATE cases SET owner_id=")
        assert "cases.owner_id IS NULL" in first and "cases.status !=" in first
    assert sum(s.startswith("UPDATE cases") for s in won + lost) == 2
    # A lost claim reads the case only after the update, to choose the 409.
    assert lost[1].startswith("SELECT cases.status")


# --- concurrency -------------------------------------------------------------------------------


def test_two_simultaneous_claims_from_two_sessions_have_exactly_one_winner(real_app, app_database):
    """Both claims wait behind a third connection that holds the case row's write lock, then
    race when it rolls back. Holding a row by updating it blocks other writers on SQLite and
    Postgres alike, so the test runs unchanged on Postgres (Deploy phase)."""
    add_case(app_database, 1)
    callers = {"analyst": ANALYST, "lead": LEAD}
    barrier = threading.Barrier(len(callers) + 1, timeout=10)  # a failing thread can't hang the run
    responses = {}

    def attempt(name):
        client = TestClient(real_app)  # each request gets its own session from get_session
        barrier.wait()
        responses[name] = claim(client, 1, headers=callers[name])

    threads = [threading.Thread(target=attempt, args=(name,)) for name in callers]
    blocker = database.create_db_engine(app_database)
    try:
        with blocker.connect() as conn:
            conn.execute(update(Case).where(Case.id == 1).values(status=Case.status))
            for thread in threads:
                thread.start()
            barrier.wait()
            time.sleep(0.5)  # well inside the 5 s busy timeout; both claims are now waiting
            conn.rollback()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        blocker.dispose()

    statuses = sorted(response.status_code for response in responses.values())
    assert statuses == [200, 409]
    (winner,) = [name for name, response in responses.items() if response.status_code == 200]
    (loser,) = [name for name, response in responses.items() if response.status_code == 409]
    assert responses[loser].json() == ALREADY_CLAIMED
    winner_id = {"analyst": ANALYST_ID, "lead": LEAD_ID}[winner]
    assert responses[winner].json()["owner"]["id"] == str(winner_id)
    assert state(app_database, 1) == (winner_id, "New", [(winner_id, "claimed")])


# --- contract ----------------------------------------------------------------------------------


def test_the_claim_route_matches_the_contract(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    contract_op = join_server_base(contract)["/api/cases/{id}/claim"]["post"]
    generated_op = generated["paths"]["/api/cases/{id}/claim"]["post"]
    assert generated_op["operationId"] == contract_op["operationId"] == "claimCase"
    assert "requestBody" not in generated_op
    assert set(generated_op["responses"]) == set(contract_op["responses"]) == {"200", "401", "404", "409", "422"}

    def schema(op, status):
        return op["responses"][status]["content"]["application/json"]["schema"]

    assert compare_schemas(contract, schema(contract_op, "200"), generated, schema(generated_op, "200")) == []
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    for status in ("401", "404", "409", "422"):
        assert diff(api_error, canonical(generated, schema(generated_op, status))) == [], status
