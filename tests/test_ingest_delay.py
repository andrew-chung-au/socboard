"""A new alert shows in `GET /api/queue` at once, even with SOCBOARD_QUEUE_CACHE_SECONDS set (QueueLatencyOverSLO).

The queue read used to reuse its last answer for the configured seconds and ingest never cleared
it, so a case ingested just after a read stayed out of the queue for up to that long (8 s in the
compose file, over the 5 s SLO). In process: no server, no sleep.
"""

import pytest
from backend_support import ANALYST_TOKEN
from fastapi.testclient import TestClient

KEY = "test-only-ingest-key-0123456789"
AUTH = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
ALERT = {
    "source": "siem-1",
    "type": "failed_login",
    "asset": "host-unknown",
    "timestamp": "2026-11-12T09:00:00Z",
    "title": "Many failed logins",
    "description": "50 failures in 5 minutes.",
}


@pytest.mark.parametrize("cache_seconds", ["0", "8"])
def test_an_ingested_case_is_in_the_very_next_queue_read(app_database, monkeypatch, cache_seconds):
    from socboard.app import create_app

    monkeypatch.setenv("SOCBOARD_INGEST_API_KEY", KEY)
    monkeypatch.setenv("SOCBOARD_QUEUE_CACHE_SECONDS", cache_seconds)
    client = TestClient(create_app())

    assert client.get("/api/queue", headers=AUTH).json()["cases"] == []  # warms any cache
    created = client.post("/api/alerts", json=ALERT, headers={"X-API-Key": KEY})
    assert created.status_code == 201

    queue = client.get("/api/queue", headers=AUTH).json()["cases"]
    assert [case["id"] for case in queue] == [created.json()["id"]]
