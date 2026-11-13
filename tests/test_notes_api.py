"""`POST /api/cases/{id}/notes` over HTTP (#18), and the notes in `GET /api/cases/{id}`.

Cases are written straight into the migrated test database with the helpers of test_queue_api.py.
The contract's order of checks is 401, schema 422, 404, 409 `case_closed`, 422 `empty_note`.
"""

from datetime import UTC, datetime
from urllib.parse import quote

import pytest
from backend_support import ANALYST_ID, ANALYST_TOKEN, LEAD_ID, LEAD_TOKEN
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from test_queue_api import (
    AUTH,
    NOT_FOUND_BODY,
    UNAUTHORIZED_BODY,
    add_case,
    count_rows,
    run,
)

from socboard.models import AuditEntry, Case, Note

LEAD_AUTH = {"Authorization": f"Bearer {LEAD_TOKEN}"}
CLOSED_BODY = {"code": "case_closed", "message": "Notes cannot be added to a closed case."}
EMPTY_BODY = {"code": "empty_note", "message": "Note text cannot be empty."}
INVALID_BODY = {"code": "validation_error", "message": "The request is not valid."}
HOSTILE = "<script>alert(1)</script>"


def post(client, case_id, payload, headers=AUTH):
    return client.post(f"/api/cases/{case_id}/notes", json=payload, headers=headers)


def audit_rows(url):
    return run(url, lambda conn: conn.execute(select(AuditEntry.__table__)).mappings().all())


def note_rows(url):
    return run(url, lambda conn: conn.execute(select(Note.__table__)).mappings().all())


def nothing_written(url):
    return count_rows(url) == {"Alert": 1, "Case": 1, "Note": 0, "AuditEntry": 0}


# --- the success path --------------------------------------------------------------------------


def test_a_note_is_created_with_the_author_and_a_utc_time(real_client, app_database):
    add_case(app_database, 1)
    before = datetime.now(UTC)
    response = post(real_client, 1, {"text": "looked at the logs"})
    after = datetime.now(UTC)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "author", "text", "createdAt"}
    assert body["text"] == "looked at the logs"
    assert body["author"] == {
        "id": str(ANALYST_ID),
        "username": "test-analyst",
        "displayName": "Test Analyst",
        "role": "analyst",
    }
    assert body["createdAt"].endswith("Z")
    assert before <= datetime.fromisoformat(body["createdAt"]) <= after
    (stored,) = note_rows(app_database)
    assert str(stored["id"]) == body["id"]
    assert (stored["case_id"], stored["author_id"]) == (1, ANALYST_ID)


def test_any_user_can_note_an_open_case_owned_by_someone_else(real_client, app_database):
    add_case(app_database, 1, status="Investigating", owner_id=LEAD_ID)
    add_case(app_database, 2, status="Investigating", owner_id=ANALYST_ID)
    assert post(real_client, 1, {"text": "analyst on the lead's case"}).status_code == 201
    response = post(real_client, 2, {"text": "lead on the analyst's case"}, LEAD_AUTH)
    assert response.status_code == 201
    assert response.json()["author"]["role"] == "lead"


@pytest.mark.parametrize("status", ["New", "Investigating"])
def test_a_note_does_not_change_the_status_or_the_owner(real_client, app_database, status):
    owner = None if status == "New" else LEAD_ID
    add_case(app_database, 1, status=status, owner_id=owner)
    assert post(real_client, 1, {"text": "x"}).status_code == 201
    (case,) = run(app_database, lambda conn: conn.execute(select(Case.__table__)).mappings().all())
    assert (case["status"], case["owner_id"], case["closing_reason"]) == (status, owner, None)


@pytest.mark.parametrize(
    "text",
    [HOSTILE, "  padded  ", "line1\nline2\t", "caf\u00e9 \U0001f600", "'; DROP TABLE notes;--", "x" * 5000],
)
def test_text_is_stored_and_returned_raw_with_its_spaces(real_client, app_database, text):
    add_case(app_database, 1)
    body = post(real_client, 1, {"text": text}).json()
    assert body["text"] == text
    assert note_rows(app_database)[0]["text"] == text
    assert real_client.get("/api/cases/1", headers=AUTH).json()["notes"][0]["text"] == text


# --- the audit entry ---------------------------------------------------------------------------


def test_a_note_writes_one_note_added_entry_that_names_the_id_and_not_the_text(
    real_client, app_database
):
    add_case(app_database, 1)
    secret = "zz-note-text-must-not-leak " + HOSTILE
    note_id = post(real_client, 1, {"text": secret}, LEAD_AUTH).json()["id"]
    (entry,) = audit_rows(app_database)
    assert (entry["case_id"], entry["actor_id"], entry["action"]) == (1, LEAD_ID, "note_added")
    assert entry["detail"] == f"Note {note_id} added"
    assert "zz-note-text" not in entry["detail"] and "script" not in entry["detail"]


def test_a_success_is_one_transaction_and_one_commit(real_app, real_client, app_database):
    add_case(app_database, 1)
    commits = []
    event.listen(real_app.state.database.engine(), "commit", lambda conn: commits.append(1))
    assert post(real_client, 1, {"text": "x"}).status_code == 201
    assert commits == [1]
    assert count_rows(app_database)["Note"] == count_rows(app_database)["AuditEntry"] == 1


def test_a_refused_audit_flush_rolls_back_the_note_and_is_not_a_409(
    real_app, app_database, monkeypatch
):
    add_case(app_database, 1)

    def refuse(*args, **kwargs):
        raise IntegrityError("INSERT", {}, Exception("refused"))

    monkeypatch.setattr("socboard.cases.append_audit", refuse)
    commits = []
    event.listen(real_app.state.database.engine(), "commit", lambda conn: commits.append(1))
    response = post(TestClient(real_app, raise_server_exceptions=False), 1, {"text": "x"})
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert commits == []
    assert nothing_written(app_database)


# --- refusals write nothing --------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", " ", "   ", "\n\t \r\n", "\u3000", "\u00a0 \u2003"])
def test_empty_or_whitespace_text_is_422_empty_note_and_stores_nothing(
    real_client, app_database, text
):
    add_case(app_database, 1)
    response = post(real_client, 1, {"text": text})
    assert (response.status_code, response.json()) == (422, EMPTY_BODY)
    assert nothing_written(app_database)


def test_5000_characters_are_accepted_and_5001_are_a_schema_422(real_client, app_database):
    add_case(app_database, 1)
    assert post(real_client, 1, {"text": "a" * 5000}).status_code == 201
    response = post(real_client, 1, {"text": "a" * 5001})
    assert (response.status_code, response.json()) == (422, INVALID_BODY)
    assert count_rows(app_database)["Note"] == 1
    assert count_rows(app_database)["AuditEntry"] == 1


def test_the_limit_counts_characters_not_bytes(real_client, app_database):
    add_case(app_database, 1)
    assert post(real_client, 1, {"text": "\u00e9" * 5000}).status_code == 201
    assert post(real_client, 1, {"text": "\U0001f600" * 5001}).status_code == 422


@pytest.mark.parametrize(
    "content",
    [
        b"{}",
        b'{"text": null}',
        b'{"text": 5}',
        b'{"text": ["a"]}',
        b'{"text": {"a": 1}}',
        b'{"text": true}',
        b'["a"]',
        b"not json",
        b"",
        b'{"text": "\\ud800"}',
        b'{"text": "ok"',
    ],
)
def test_a_body_that_fails_the_schema_is_422_validation_error_and_stores_nothing(
    real_client, app_database, content
):
    add_case(app_database, 1)
    response = real_client.post(
        "/api/cases/1/notes", content=content, headers={**AUTH, "Content-Type": "application/json"}
    )
    assert (response.status_code, response.json()) == (422, INVALID_BODY)
    assert nothing_written(app_database)


def test_a_note_on_a_closed_case_is_409_case_closed_and_stores_nothing(real_client, app_database):
    add_case(app_database, 1, status="Closed", closing_reason="duplicate")
    response = post(real_client, 1, {"text": "too late"})
    assert (response.status_code, response.json()) == (409, CLOSED_BODY)
    assert count_rows(app_database) == {"Alert": 1, "Case": 1, "Note": 0, "AuditEntry": 0}


@pytest.mark.parametrize(
    "case_id",
    [
        "999", "abc", "0", "-1", "01", "1.5", "+1", "1 ", " 1", "1 OR 1=1", "1;--",
        "9223372036854775808", "99999999999999999999", "\uff11", "C-1001",
    ],
)  # fmt: skip
def test_an_unknown_or_invalid_id_is_404_not_found_not_500(real_client, app_database, case_id):
    add_case(app_database, 1)
    response = post(real_client, quote(case_id, safe=""), {"text": "x"})
    assert (response.status_code, response.json()) == (404, NOT_FOUND_BODY)
    assert nothing_written(app_database)


# --- the order of checks -----------------------------------------------------------------------

BAD_HEADERS = [
    {},
    {"Authorization": "Bearer not-a-token"},
    {"Authorization": f"Basic {ANALYST_TOKEN}"},
    {"Authorization": ANALYST_TOKEN},
]


@pytest.mark.parametrize("headers", BAD_HEADERS)
@pytest.mark.parametrize("case_id", ["1", "999", "abc"])
@pytest.mark.parametrize("payload", [{"text": "x"}, {"text": " "}, {"nope": 1}])
def test_a_missing_or_bad_token_is_401_before_everything_else(
    real_client, app_database, headers, case_id, payload
):
    add_case(app_database, 1, status="Closed", closing_reason="duplicate")
    response = post(real_client, case_id, payload, headers)
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY
    assert response.headers["www-authenticate"] == "Bearer"
    assert count_rows(app_database)["Note"] == 0


def test_a_schema_error_comes_before_404_and_409(real_client, app_database):
    add_case(app_database, 1, status="Closed", closing_reason="duplicate")
    assert post(real_client, 999, {"text": 5}).json() == INVALID_BODY
    assert post(real_client, 1, {"text": 5}).json() == INVALID_BODY
    assert post(real_client, 1, {"text": "a" * 5001}).json() == INVALID_BODY


def test_404_comes_before_empty_note_and_409_before_empty_note(real_client, app_database):
    add_case(app_database, 1, status="Closed", closing_reason="duplicate")
    assert post(real_client, 999, {"text": " "}).json() == NOT_FOUND_BODY
    assert post(real_client, 1, {"text": " "}).json() == CLOSED_BODY


# --- reading the notes back --------------------------------------------------------------------


def test_three_quick_notes_come_back_in_creation_order(real_client, app_database):
    add_case(app_database, 1)
    add_case(app_database, 2)
    ids = [post(real_client, 1, {"text": f"note {n}"}).json()["id"] for n in (1, 2, 3)]
    post(real_client, 2, {"text": "another case"}, LEAD_AUTH)
    assert [int(i) for i in ids] == sorted(int(i) for i in ids)
    notes = real_client.get("/api/cases/1", headers=AUTH).json()["notes"]
    assert [note["text"] for note in notes] == ["note 1", "note 2", "note 3"]
    assert [note["id"] for note in notes] == ids
    assert real_client.get("/api/cases/2", headers=AUTH).json()["notes"][0]["author"]["role"] == "lead"
