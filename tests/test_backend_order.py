"""Order of checks for a request that is not well-formed (openapi.yaml, #9).

Contract order: 401, then a role check that needs no case lookup (403), then the generic schema
422, then the case-bound checks. This holds only because authentication and the role check are
route dependencies (tests/backend_support.py), never inline in a handler.

These tests are the repository's proof of the contract's TESTED claim (fastapi 0.162.4,
starlette 1.10.0, pydantic 2.14.3). The contract's probe is not in the repository. Keep them, and
do not weaken them, when #13 and #14 replace the test-only dependencies with the real ones.
Each body is exactly `{ code, message }`.
"""

from backend_support import ANALYST_ID, ANALYST_TOKEN, KNOWN_CASE_ID, LEAD_TOKEN
from test_queue_api import add_case


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def check(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == code


# --- GET /cases (the closed-cases list) --------------------------------------------------------


def test_cases_a_no_token_and_no_status_is_401(client):
    check(client.get("/api/cases"), 401, "unauthorized")


def test_cases_b_analyst_without_status_is_403_not_422(client):
    check(client.get("/api/cases", headers=bearer(ANALYST_TOKEN)), 403, "forbidden")


def test_cases_c_analyst_with_status_open_is_403(client):
    response = client.get("/api/cases", params={"status": "Open"}, headers=bearer(ANALYST_TOKEN))
    check(response, 403, "forbidden")


def test_cases_d_lead_without_status_is_422(client):
    check(client.get("/api/cases", headers=bearer(LEAD_TOKEN)), 422, "validation_error")


def test_cases_lead_with_status_closed_is_200(client):
    response = client.get("/api/cases", params={"status": "Closed"}, headers=bearer(LEAD_TOKEN))
    assert response.status_code == 200


# --- POST /cases/{id}/transition ---------------------------------------------------------------

BAD_BODY = {"nope": 1}
GOOD_BODY = {"to": "Investigating"}


def test_transition_a_invalid_body_and_no_token_is_401(client):
    response = client.post(f"/api/cases/{KNOWN_CASE_ID}/transition", json=BAD_BODY)
    check(response, 401, "unauthorized")


def test_transition_b_invalid_body_analyst_known_case_is_422(client):
    response = client.post(
        f"/api/cases/{KNOWN_CASE_ID}/transition", json=BAD_BODY, headers=bearer(ANALYST_TOKEN)
    )
    check(response, 422, "validation_error")


def test_transition_c_invalid_body_unknown_case_is_422_not_404(client):
    response = client.post(
        "/api/cases/C-unknown/transition", json=BAD_BODY, headers=bearer(ANALYST_TOKEN)
    )
    check(response, 422, "validation_error")


def test_transition_d_body_that_is_not_json_and_no_token_is_422(client):
    response = client.post(
        f"/api/cases/{KNOWN_CASE_ID}/transition",
        content="{this is not json",
        headers={"Content-Type": "application/json"},
    )
    check(response, 422, "validation_error")


def test_transition_well_formed_request_keeps_the_usual_order(client, app_database):
    # The real route (#17) on a real case owned by the analyst; KNOWN_CASE_ID is never a real id.
    add_case(app_database, 1, owner_id=ANALYST_ID)
    url = "/api/cases/C-unknown/transition"
    check(client.post(url, json=GOOD_BODY), 401, "unauthorized")
    check(client.post(url, json=GOOD_BODY, headers=bearer(ANALYST_TOKEN)), 404, "not_found")
    ok = client.post("/api/cases/1/transition", json=GOOD_BODY, headers=bearer(ANALYST_TOKEN))
    assert ok.status_code == 200
