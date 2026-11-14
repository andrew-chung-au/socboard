"""`GET /api/cases/{id}/audit` (`getAudit`, #19): the trail, Lead-only, read-only, no secrets.

The journey test goes through the real routes (login, ingest with the API key, claim, move, note,
close). Ordering and access tests write cases straight into the migrated test database with the
helpers of test_queue_api.py. The order of checks is 401, 403, 404 (`require_lead` is a route
dependency, so an Analyst asking about an unknown id gets 403).
"""

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from backend_support import (
    ANALYST_ID,
    ANALYST_PASSWORD,
    ANALYST_TOKEN,
    ANALYST_USERNAME,
    LEAD_PASSWORD,
    LEAD_TOKEN,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_queue_api import NOT_FOUND_BODY, UNAUTHORIZED_BODY, add_case, run

from socboard import database
from socboard.audit import append_audit
from socboard.audit_guard import AuditImmutableError
from socboard.models import AuditEntry

PACKAGE = Path(__file__).resolve().parents[1] / "socboard"
LEAD = {"Authorization": f"Bearer {LEAD_TOKEN}"}
ANALYST = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
FORBIDDEN_BODY = {"code": "forbidden", "message": "You do not have permission to do this."}
# Synthetic, test-only ingest key (the minimum length is 16).
INGEST_KEY = "test-only-ingest-key-0123456789"
BASE = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def audit(client, case_id, headers=LEAD):
    return client.get(f"/api/cases/{case_id}/audit", headers=headers)


def add_entries(url, case_id, entries):
    """Append (action, actor_id, detail, at) entries in the given order, through `append_audit`."""
    engine = database.create_db_engine(url)
    try:
        with Session(engine) as session:
            for action, actor_id, detail, at in entries:
                append_audit(
                    session, case_id=case_id, actor_id=actor_id, action=action, detail=detail, at=at
                )
            session.commit()
    finally:
        engine.dispose()


def stored_entries(url):
    return run(url, lambda conn: [tuple(row) for row in conn.execute(select(AuditEntry.__table__))])


# --- the full journey, and no secret in any entry -----------------------------------------------


@pytest.fixture
def journey(app_database, monkeypatch):
    """Login, ingest with the API key, claim, move, note and close; returns what a test needs."""
    from socboard.app import create_app

    monkeypatch.setenv("SOCBOARD_INGEST_API_KEY", INGEST_KEY)
    # The app must be built after the ingest key is set, so no shared crash fixture.
    client = TestClient(create_app(), raise_server_exceptions=False)

    login = client.post(
        "/api/login", json={"username": ANALYST_USERNAME, "password": ANALYST_PASSWORD}
    )
    assert login.status_code == 200
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    ingest = client.post(
        "/api/alerts",
        headers={"X-API-Key": INGEST_KEY},
        json={
            "source": "siem-1",
            "type": "failed_login",
            "asset": "host-unknown",
            "timestamp": "2026-11-12T09:00:00Z",
            "title": "Many failed logins",
            "description": "50 failures in 5 minutes.",
        },
    )
    assert ingest.status_code == 201
    case = ingest.json()
    case_id = case["id"]

    assert client.post(f"/api/cases/{case_id}/claim", headers=headers).status_code == 200
    move = client.post(
        f"/api/cases/{case_id}/transition", headers=headers, json={"to": "Investigating"}
    )
    assert move.status_code == 200
    # The note text holds the token: the entry must name the note, never its text.
    note = client.post(
        f"/api/cases/{case_id}/notes", headers=headers, json={"text": f"saw token {token}"}
    )
    assert note.status_code == 201
    close = client.post(
        f"/api/cases/{case_id}/transition",
        headers=headers,
        json={"to": "Closed", "closingReason": "true_positive"},
    )
    assert close.status_code == 200
    return {
        "client": client,
        "url": app_database,
        "case_id": case_id,
        "severity": case["severity"],
        "note_id": note.json()["id"],
        "token": token,
    }


def test_the_full_journey_gives_exactly_five_entries_in_order(journey):
    response = audit(journey["client"], journey["case_id"])
    assert response.status_code == 200
    entries = response.json()
    analyst = {
        "id": str(ANALYST_ID),
        "username": ANALYST_USERNAME,
        "displayName": "Test Analyst",
        "role": "analyst",
    }
    assert [(e["actor"], e["action"], e["detail"]) for e in entries] == [
        ("system", "ingested", f"Alert ingested from siem-1; severity {journey['severity']}"),
        (analyst, "claimed", "Case claimed."),
        (analyst, "status_changed", "New -> Investigating"),
        (analyst, "note_added", f"Note {journey['note_id']} added"),
        (analyst, "status_changed", "Investigating -> Closed (true_positive)"),
    ]
    assert all(set(entry) == {"id", "at", "actor", "action", "detail"} for entry in entries)
    ids = [int(entry["id"]) for entry in entries]
    assert ids == sorted(ids)
    times = [datetime.fromisoformat(entry["at"].replace("Z", "+00:00")) for entry in entries]
    assert times == sorted(times)


def test_no_entry_holds_the_token_the_api_key_or_a_password(journey):
    # Every entry in the database (the journey made the only ones) and the API answer.
    stored = json.dumps(stored_entries(journey["url"]), default=str)
    answered = audit(journey["client"], journey["case_id"]).text
    assert len(stored_entries(journey["url"])) == 5
    for secret in (journey["token"], LEAD_TOKEN, ANALYST_TOKEN, INGEST_KEY, ANALYST_PASSWORD, LEAD_PASSWORD):
        assert secret not in stored
        assert secret not in answered


# --- order: time, then id -----------------------------------------------------------------------


def test_entries_are_ordered_by_time_then_id(real_client, app_database):
    add_case(app_database, 1)
    one_hour = BASE + timedelta(hours=1)
    add_entries(
        app_database,
        1,
        [
            ("note_added", ANALYST_ID, "d0", one_hour),
            ("note_added", ANALYST_ID, "d1", BASE),
            ("note_added", ANALYST_ID, "d2", one_hour),
            ("note_added", ANALYST_ID, "d3", BASE),
        ],
    )
    response = audit(real_client, 1)
    assert response.status_code == 200
    assert [entry["detail"] for entry in response.json()] == ["d1", "d3", "d0", "d2"]


def test_only_the_cases_own_entries_are_returned(real_client, app_database):
    add_case(app_database, 1)
    add_case(app_database, 2)
    add_entries(app_database, 1, [("note_added", ANALYST_ID, "mine", BASE)])
    add_entries(app_database, 2, [("note_added", ANALYST_ID, "other", BASE)])
    assert [e["detail"] for e in audit(real_client, 1).json()] == ["mine"]


def test_a_case_without_entries_gives_an_empty_array(real_client, app_database):
    add_case(app_database, 1)
    response = audit(real_client, 1)
    assert (response.status_code, response.json()) == (200, [])


def test_reading_writes_nothing(real_client, app_database):
    add_case(app_database, 1)
    add_entries(app_database, 1, [("note_added", ANALYST_ID, "d", BASE)])
    before = stored_entries(app_database)
    assert audit(real_client, 1).status_code == 200
    assert stored_entries(app_database) == before


# --- access: 401, then 403, then 404 ------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer not-a-token"}])
@pytest.mark.parametrize("case_id", ["1", "999"])
def test_no_valid_token_is_401_even_for_an_unknown_case(real_client, app_database, headers, case_id):
    add_case(app_database, 1)
    response = audit(real_client, case_id, headers)
    assert (response.status_code, response.json()) == (401, UNAUTHORIZED_BODY)


@pytest.mark.parametrize("case_id", ["1", "999", "abc", "0"])
def test_an_analyst_is_403_whether_or_not_the_case_exists(real_client, app_database, case_id):
    add_case(app_database, 1)
    response = audit(real_client, case_id, ANALYST)
    assert (response.status_code, response.json()) == (403, FORBIDDEN_BODY)


@pytest.mark.parametrize("case_id", ["999", "abc", "0", "01", "-1", "99999999999999999999"])
def test_a_lead_gets_404_for_an_unknown_or_malformed_id(real_client, app_database, case_id):
    add_case(app_database, 1)
    response = audit(real_client, case_id)
    assert (response.status_code, response.json()) == (404, NOT_FOUND_BODY)


# --- no write route -----------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["PUT", "PATCH", "POST", "DELETE"])
@pytest.mark.parametrize("headers", [LEAD, ANALYST, {}])
def test_a_write_to_the_audit_path_is_405(real_client, app_database, method, headers):
    add_case(app_database, 1)
    add_entries(app_database, 1, [("note_added", ANALYST_ID, "d", BASE)])
    before = stored_entries(app_database)
    response = real_client.request(method, "/api/cases/1/audit", json={"detail": "x"}, headers=headers)
    assert response.status_code == 405
    assert set(response.json()) == {"code", "message"}
    assert response.json()["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]
    assert stored_entries(app_database) == before


def test_the_data_layer_refuses_to_change_or_remove_an_entry_after_a_journey(journey):
    # Builds on #11 (tests/test_data_audit.py): the ORM guard and the database triggers.
    url = journey["url"]
    before = stored_entries(url)
    engine = database.create_db_engine(url)
    try:
        with Session(engine) as session:
            entry = session.scalars(select(AuditEntry).order_by(AuditEntry.id)).first()
            entry.detail = "changed"
            with pytest.raises(AuditImmutableError):
                session.flush()
            session.rollback()
            session.delete(session.scalars(select(AuditEntry).order_by(AuditEntry.id)).first())
            with pytest.raises(AuditImmutableError):
                session.flush()
            session.rollback()
        for sql in ("UPDATE audit_entries SET detail = 'raw'", "DELETE FROM audit_entries"):
            with engine.connect() as conn:
                with pytest.raises(IntegrityError, match="Audit entries cannot be"):
                    conn.exec_driver_sql(sql)
                conn.rollback()
    finally:
        engine.dispose()
    assert stored_entries(url) == before
    assert len(audit(journey["client"], journey["case_id"]).json()) == 5


# --- code search: nothing in socboard/ updates or deletes an audit entry ------------------------

WRITE_CALLS = {"update", "delete", "merge", "bulk_update_mappings", "bulk_save_objects"}
RAW_SQL_CALLS = {"text", "exec_driver_sql", "execute"}
# The guard and the model name the table to refuse changes to it; migrations are not code paths.
EXEMPT = {"audit_guard.py", "models.py"}  # top-level only: compared as a path under socboard/


def _names_model(node):
    return any(
        (isinstance(n, ast.Name) and n.id == "AuditEntry")
        or (isinstance(n, ast.Attribute) and n.attr == "AuditEntry")
        for n in ast.walk(node)
    )


def _names_table(node):
    return any(
        isinstance(n, ast.Constant) and isinstance(n.value, str) and "audit_entries" in n.value
        for n in ast.walk(node)
    )


def _call_name(call):
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)


def _audit_writers(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        # `delete` and `merge` take an instance, so any use is refused; `update` names its table.
        if name in {"delete", "merge"} or (name in WRITE_CALLS and _names_model(node)):
            yield node
        elif name in RAW_SQL_CALLS and _names_table(node):
            yield node


def test_no_code_path_updates_or_deletes_an_audit_entry():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        if relative.parts[0] == "migrations" or relative.as_posix() in EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{relative}:{node.lineno}" for node in _audit_writers(tree)]
    assert offenders == []


def test_the_code_search_would_notice_a_writer():
    for source in (
        "session.execute(update(AuditEntry).values(detail='x'))",
        "session.execute(delete(models.AuditEntry))",
        "session.delete(entry)",
        "session.merge(entry)",
        "conn.execute(text('DELETE FROM audit_entries'))",
        "conn.exec_driver_sql('UPDATE audit_entries SET detail = 1')",
    ):
        assert list(_audit_writers(ast.parse(source))), source
    assert not list(_audit_writers(ast.parse("session.execute(update(Case).values(status='x'))")))
