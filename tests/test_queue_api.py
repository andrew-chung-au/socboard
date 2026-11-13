"""`GET /api/queue` and `GET /api/cases/{id}` over HTTP (#15).

Cases are written straight into the migrated test database (so a test controls severity, time and
ids), except the latency test, which ingests through `POST /api/alerts`.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from backend_support import ANALYST_ID, ANALYST_TOKEN, LEAD_ID, LEAD_TOKEN
from fastapi.testclient import TestClient
from sqlalchemy import event, insert, update

from socboard import auth, database
from socboard.models import Alert, AuditEntry, Case, Note

AUTH = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
UNAUTHORIZED_BODY = {"code": "unauthorized", "message": auth.UNAUTHORIZED_MESSAGE}
NOT_FOUND_BODY = {"code": "not_found", "message": "Case not found."}
BASE = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
SUMMARY_FIELDS = {
    "id", "title", "alertType", "source", "asset", "assetCriticality", "severity", "status",
    "owner", "alertTimestamp", "ingestedAt", "closingReason",
}  # fmt: skip


def run(url, work):
    engine = database.create_db_engine(url)
    try:
        with engine.begin() as conn:
            return work(conn)
    finally:
        engine.dispose()


def add_case(
    url,
    case_id,
    severity="Low",
    minutes=0,
    status="New",
    owner_id=None,
    closing_reason=None,
    **alert_fields,
):
    """One alert and one case with a chosen id; the alert time is BASE plus `minutes`."""
    alert = {
        "id": case_id,
        "source": "siem-1",
        "alert_type": "failed_login",
        "asset": "host-1",
        "asset_criticality": "Medium",
        "title": f"title {case_id}",
        "description": f"description {case_id}",
        "alert_timestamp": BASE + timedelta(minutes=minutes),
        "ingested_at": BASE + timedelta(hours=1),
        "severity": severity,
        "severity_rule": f"rule {case_id}",
    }
    alert.update(alert_fields)

    def work(conn):
        conn.execute(insert(Alert.__table__).values(alert))
        conn.execute(
            insert(Case.__table__).values(
                id=case_id,
                alert_id=case_id,
                status=status,
                owner_id=owner_id,
                closing_reason=closing_reason,
            )
        )

    run(url, work)


def count_rows(url):
    def work(conn):
        return {
            model.__name__: len(conn.execute(model.__table__.select()).all())
            for model in (Alert, Case, Note, AuditEntry)
        }

    return run(url, work)


def queue(client, headers=AUTH):
    response = client.get("/api/queue", headers=headers)
    assert response.status_code == 200
    return response.json()


def queue_ids(client):
    return [entry["id"] for entry in queue(client)["cases"]]


# --- the queue ---------------------------------------------------------------------------------


def test_an_empty_queue_is_an_empty_list_with_a_serve_time(real_client):
    before = datetime.now(UTC)
    body = queue(real_client)
    after = datetime.now(UTC)
    assert set(body) == {"cases", "servedAt"}
    assert body["cases"] == []
    served = datetime.fromisoformat(body["servedAt"])
    assert body["servedAt"].endswith("Z") and before <= served <= after


def test_the_queue_holds_claimed_and_unclaimed_cases_that_are_not_closed(real_client, app_database):
    add_case(app_database, 1, status="New")
    add_case(app_database, 2, status="Investigating", owner_id=LEAD_ID)
    add_case(app_database, 3, status="Closed", closing_reason="duplicate")
    assert sorted(queue_ids(real_client)) == ["1", "2"]


def test_severity_is_ranked_critical_first_and_not_alphabetically(real_client, app_database):
    # Alphabetical order would be Critical, High, Low, Medium. The oldest alert is the Low one,
    # so the time key alone would put it first.
    for case_id, severity, minutes in [(1, "Low", 0), (2, "Medium", 1), (3, "High", 2), (4, "Critical", 3)]:
        add_case(app_database, case_id, severity=severity, minutes=minutes)
    assert queue_ids(real_client) == ["4", "3", "2", "1"]
    severities = [entry["severity"] for entry in queue(real_client)["cases"]]
    assert severities == ["Critical", "High", "Medium", "Low"]


def test_within_a_severity_the_oldest_alert_is_first_by_alert_time_not_ingest_time(
    real_client, app_database
):
    # Case 1 has the newest alert but was ingested first; the alert's own time decides.
    add_case(app_database, 1, severity="High", minutes=30, ingested_at=BASE)
    add_case(app_database, 2, severity="High", minutes=10, ingested_at=BASE + timedelta(hours=2))
    add_case(app_database, 3, severity="High", minutes=20, ingested_at=BASE + timedelta(hours=1))
    assert queue_ids(real_client) == ["2", "3", "1"]


def test_ties_on_severity_and_time_end_in_case_id_ascending_and_are_repeatable(
    real_client, app_database
):
    # Inserted in the opposite order to the expected one, so insertion order cannot explain it.
    for case_id in (30, 20, 10):
        add_case(app_database, case_id, severity="High", minutes=5)
    add_case(app_database, 40, severity="Critical", minutes=99)
    add_case(app_database, 5, severity="High", minutes=6)
    expected = ["40", "10", "20", "30", "5"]
    assert queue_ids(real_client) == expected
    assert queue_ids(real_client) == expected


def test_an_entry_carries_the_summary_fields(real_client, app_database):
    add_case(
        app_database, 7, severity="High", minutes=3, owner_id=ANALYST_ID, status="Investigating",
        title="T", source="edr", alert_type="malware_detection", asset="srv-1",
        asset_criticality="High",
    )  # fmt: skip
    (entry,) = queue(real_client)["cases"]
    assert set(entry) == SUMMARY_FIELDS
    assert entry["id"] == "7"
    assert (entry["title"], entry["source"], entry["alertType"]) == ("T", "edr", "malware_detection")
    assert (entry["asset"], entry["assetCriticality"]) == ("srv-1", "High")
    assert (entry["severity"], entry["status"], entry["closingReason"]) == ("High", "Investigating", None)
    assert entry["alertTimestamp"] == "2026-11-12T09:03:00Z"
    assert entry["ingestedAt"] == "2026-11-12T10:00:00Z"
    assert entry["owner"] == {
        "id": str(ANALYST_ID),
        "username": "test-analyst",
        "displayName": "Test Analyst",
        "role": "analyst",
    }


def test_an_unclaimed_case_has_a_null_owner_and_no_password_hash_is_returned(
    real_client, app_database
):
    add_case(app_database, 1)
    add_case(app_database, 2, owner_id=LEAD_ID, status="Investigating")
    response = real_client.get("/api/queue", headers=AUTH)
    owners = {entry["id"]: entry["owner"] for entry in response.json()["cases"]}
    assert owners["1"] is None and owners["2"]["username"] == "test-lead"
    assert "password" not in response.text


def test_an_ingested_alert_is_in_the_next_queue_with_ingested_at_before_served_at(
    app_database, monkeypatch
):
    from socboard.app import create_app

    key = "test-only-ingest-key-0123456789"
    monkeypatch.setenv("SOCBOARD_INGEST_API_KEY", key)
    client = TestClient(create_app())
    body = {
        "source": "siem-1",
        "type": "malware_detection",
        "asset": "host-x",
        "timestamp": "2026-11-12T09:00:00Z",
        "title": "Found",
        "description": "d",
    }
    created = client.post("/api/alerts", json=body, headers={"X-API-Key": key})
    assert created.status_code == 201
    result = queue(client)
    (entry,) = [e for e in result["cases"] if e["id"] == created.json()["id"]]
    assert entry == created.json()
    assert datetime.fromisoformat(entry["ingestedAt"]) < datetime.fromisoformat(result["servedAt"])


def test_a_case_closed_directly_in_the_database_leaves_the_queue(real_client, app_database):
    add_case(app_database, 1)
    add_case(app_database, 2)
    assert queue_ids(real_client) == ["1", "2"]
    run(
        app_database,
        lambda conn: conn.execute(
            update(Case.__table__)
            .where(Case.__table__.c.id == 1)
            .values(status="Closed", closing_reason="false_positive")
        ),
    )
    assert queue_ids(real_client) == ["2"]


def test_hostile_alert_text_comes_back_unchanged(real_client, app_database):
    text = "<script>alert(1)</script>"
    add_case(app_database, 1, title=text, source=text, alert_type=text, asset=text)
    (entry,) = queue(real_client)["cases"]
    assert (entry["title"], entry["source"], entry["alertType"], entry["asset"]) == (text,) * 4


# --- the case ----------------------------------------------------------------------------------


def test_the_case_detail_is_the_summary_plus_description_rule_and_notes(real_client, app_database):
    add_case(app_database, 7, severity="High", minutes=3, owner_id=ANALYST_ID, status="Investigating")
    response = real_client.get("/api/cases/7", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == SUMMARY_FIELDS | {"description", "severityRule", "notes"}
    assert body["id"] == "7"
    assert body["description"] == "description 7"
    assert body["severityRule"] == "rule 7"
    assert body["notes"] == []
    (entry,) = queue(real_client)["cases"]
    assert {key: body[key] for key in SUMMARY_FIELDS} == entry


def test_a_closed_case_can_be_opened(real_client, app_database):
    add_case(app_database, 3, status="Closed", closing_reason="true_positive")
    response = real_client.get("/api/cases/3", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "Closed"
    assert response.json()["closingReason"] == "true_positive"


def test_the_lead_can_open_a_case_too(real_client, app_database):
    add_case(app_database, 3)
    headers = {"Authorization": f"Bearer {LEAD_TOKEN}"}
    assert real_client.get("/api/cases/3", headers=headers).status_code == 200


def test_notes_come_oldest_first_with_the_note_id_as_the_tie_break(real_client, app_database):
    add_case(app_database, 1)
    add_case(app_database, 2)
    rows = [
        {"id": 5, "case_id": 1, "author_id": LEAD_ID, "text": "late", "created_at": BASE + timedelta(minutes=9)},
        {"id": 9, "case_id": 1, "author_id": ANALYST_ID, "text": "same time, higher id", "created_at": BASE},
        {"id": 8, "case_id": 1, "author_id": ANALYST_ID, "text": " same time, lower id ", "created_at": BASE},
        {"id": 6, "case_id": 2, "author_id": LEAD_ID, "text": "other case", "created_at": BASE},
    ]
    run(app_database, lambda conn: conn.execute(insert(Note.__table__), rows))
    notes = real_client.get("/api/cases/1", headers=AUTH).json()["notes"]
    assert [note["id"] for note in notes] == ["8", "9", "5"]
    assert notes[0]["text"] == " same time, lower id "
    assert notes[0]["createdAt"] == "2026-11-12T09:00:00Z"
    assert notes[0]["author"]["username"] == "test-analyst"
    assert set(notes[0]) == {"id", "author", "text", "createdAt"}


def test_hostile_alert_text_comes_back_unchanged_in_the_detail(real_client, app_database):
    text = "<script>alert(1)</script>"
    add_case(app_database, 1, title=text, description=text, severity_rule=text)
    body = real_client.get("/api/cases/1", headers=AUTH).json()
    assert (body["title"], body["description"], body["severityRule"]) == (text,) * 3


@pytest.mark.parametrize(
    "case_id",
    [
        "999", "abc", "0", "-1", "01", "1.5", "+1", "1 ", " 1", "1 OR 1=1", "1;--",
        "9223372036854775808", "99999999999999999999", "\uff11", "C-1001",
    ],
)  # fmt: skip
def test_an_unknown_or_invalid_id_is_404_not_found_not_500(real_client, app_database, case_id):
    add_case(app_database, 1)
    response = real_client.get("/api/cases/" + quote(case_id, safe=""), headers=AUTH)
    assert response.status_code == 404
    assert response.json() == NOT_FOUND_BODY


# --- authentication ----------------------------------------------------------------------------

ROUTES = ["/api/queue", "/api/cases/1", "/api/cases/unknown"]
BAD_HEADERS = [
    {},
    {"Authorization": "Bearer not-a-token"},
    {"Authorization": f"Basic {ANALYST_TOKEN}"},
    {"Authorization": ANALYST_TOKEN},
]


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("headers", BAD_HEADERS)
def test_a_missing_or_bad_token_is_401_before_anything_else(real_client, app_database, path, headers):
    add_case(app_database, 1)
    response = real_client.get(path, headers=headers)
    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY
    assert response.headers["www-authenticate"] == "Bearer"


# --- read only ---------------------------------------------------------------------------------


def test_the_reads_write_nothing_and_add_no_audit_entry(real_app, real_client, app_database):
    add_case(app_database, 1)
    add_case(app_database, 2, status="Closed", closing_reason="duplicate")
    before = count_rows(app_database)
    commits = []
    event.listen(real_app.state.database.engine(), "commit", lambda conn: commits.append(1))
    for path in ("/api/queue", "/api/cases/1", "/api/cases/2", "/api/cases/999"):
        real_client.get(path, headers=AUTH)
    assert count_rows(app_database) == before
    assert before["AuditEntry"] == 0
    assert commits == []
