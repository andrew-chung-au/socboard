"""`GET /api/cases?status=Closed` (`getClosedCases`, #20): the Lead-only list of closed cases.

One test follows each listed case to the case read and to its audit trail (#19).
"""

# Closed cases (#20)
import time
from datetime import timedelta

import pytest
from backend_support import ANALYST_ID, ANALYST_TOKEN, LEAD_ID, LEAD_TOKEN, TEST_TOKEN_SECRET, load_contract
from openapi_compare import canonical, compare_schemas, diff, join_server_base
from sqlalchemy import event, insert, update
from test_queue_api import BASE, SUMMARY_FIELDS, add_case, count_rows, run

from socboard import auth
from socboard.models import AuditEntry, Case
from socboard.tokens import create_token

URL = "/api/cases"
LEAD = {"Authorization": f"Bearer {LEAD_TOKEN}"}
ANALYST = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
FORBIDDEN_BODY = {"code": "forbidden", "message": auth.FORBIDDEN_MESSAGE}
UNAUTHORIZED_BODY = {"code": "unauthorized", "message": auth.UNAUTHORIZED_MESSAGE}
VALIDATION_BODY = {"code": "validation_error", "message": "The request is not valid."}
EXPIRED = {
    "Authorization": "Bearer "
    + create_token(LEAD_ID, TEST_TOKEN_SECRET, now=time.time() - 9 * 60 * 60)
}


def close(url, case_id, minutes, reason="true_positive", action="status_changed"):
    """Close case `case_id` (an existing open case) with a closing entry `minutes` after BASE."""
    def work(conn):
        conn.execute(
            update(Case.__table__)
            .where(Case.__table__.c.id == case_id)
            .values(status="Closed", closing_reason=reason)
        )
        conn.execute(
            insert(AuditEntry.__table__).values(
                case_id=case_id,
                actor_id=LEAD_ID,
                action=action,
                detail=f"Investigating -> Closed ({reason})",
                at=BASE + timedelta(minutes=minutes),
            )
        )

    run(url, work)


def closed(client, headers=LEAD):
    response = client.get(URL, params={"status": "Closed"}, headers=headers)
    assert response.status_code == 200
    return response.json()


def test_a_lead_gets_closed_cases_most_recently_closed_first_with_the_id_tie_break(
    real_client, app_database
):
    for case_id in (1, 2, 3, 4):
        add_case(app_database, case_id, owner_id=ANALYST_ID, status="Investigating")
    close(app_database, 1, 30, "duplicate")
    close(app_database, 2, 10, "false_positive")
    close(app_database, 3, 30, "true_positive")  # same time as case 1: higher id first
    close(app_database, 4, 20, "true_positive")
    body = closed(real_client)
    assert [entry["id"] for entry in body] == ["3", "1", "4", "2"]
    assert [entry["closingReason"] for entry in body] == [
        "true_positive", "duplicate", "true_positive", "false_positive",
    ]  # fmt: skip
    assert all(set(entry) == SUMMARY_FIELDS and entry["status"] == "Closed" for entry in body)


def test_a_later_entry_that_only_looks_like_a_close_does_not_set_the_close_time(
    real_client, app_database
):
    # Another action, or detail text that only looks like a close, never sets the close time.
    add_case(app_database, 1, status="Investigating")
    add_case(app_database, 2, status="Investigating")
    close(app_database, 1, 5)
    close(app_database, 2, 1)
    run(
        app_database,
        lambda conn: conn.execute(
            insert(AuditEntry.__table__).values(
                case_id=2,
                actor_id=LEAD_ID,
                action="note_added",
                detail="Investigating -> Closed (%)",
                at=BASE + timedelta(minutes=99),
            )
        ),
    )
    assert [entry["id"] for entry in closed(real_client)] == ["1", "2"]


def test_a_closed_case_with_no_closing_entry_is_listed_last_by_id(real_client, app_database):
    add_case(app_database, 1, status="Closed", closing_reason="duplicate")
    add_case(app_database, 2, status="Closed", closing_reason="duplicate")
    add_case(app_database, 3, status="Investigating")
    close(app_database, 3, 1)
    assert [entry["id"] for entry in closed(real_client)] == ["3", "2", "1"]


def test_no_closed_cases_is_an_empty_list(real_client, app_database):
    assert closed(real_client) == []


def test_open_claimed_and_closed_cases_together_list_only_the_closed(real_client, app_database):
    add_case(app_database, 1)  # New
    add_case(app_database, 2, owner_id=ANALYST_ID)  # claimed, New
    add_case(app_database, 3, owner_id=ANALYST_ID, status="Investigating")
    add_case(app_database, 4, owner_id=ANALYST_ID, status="Investigating")
    close(app_database, 4, 1)
    assert [entry["id"] for entry in closed(real_client)] == ["4"]


def test_hostile_alert_text_comes_back_as_stored(real_client, app_database):
    text = "<script>alert(1)</script>"
    add_case(app_database, 1, status="Investigating", title=text, source=text, asset=text)
    close(app_database, 1, 1)
    (entry,) = closed(real_client)
    assert (entry["title"], entry["source"], entry["asset"]) == (text,) * 3


def test_a_listed_case_opens_with_the_case_read(real_client, app_database):
    add_case(app_database, 5, owner_id=ANALYST_ID, status="Investigating")
    close(app_database, 5, 1, "false_positive")
    (entry,) = closed(real_client)
    response = real_client.get(f"{URL}/{entry['id']}", headers=LEAD)
    assert response.status_code == 200
    assert {key: response.json()[key] for key in SUMMARY_FIELDS} == entry


def test_each_listed_case_opens_and_its_trail_ends_with_its_real_closing_entry(
    real_client, app_database
):
    for case_id in (1, 2, 3):
        add_case(app_database, case_id, owner_id=ANALYST_ID, status="Investigating")
    # Close two through the real route as a Lead; case 2 stays open and is never listed.
    reasons = {"1": "duplicate", "3": "false_positive"}
    for case_id, reason in reasons.items():
        moved = real_client.post(
            f"{URL}/{case_id}/transition",
            headers=LEAD,
            json={"to": "Closed", "closingReason": reason},
        )
        assert moved.status_code == 200
    listed = closed(real_client)
    assert sorted(entry["id"] for entry in listed) == ["1", "3"]
    close_times = []
    for entry in listed:
        case = real_client.get(f"{URL}/{entry['id']}", headers=LEAD)
        assert case.status_code == 200
        assert case.json()["id"] == entry["id"]
        assert case.json()["status"] == "Closed"
        assert case.json()["closingReason"] == entry["closingReason"] == reasons[entry["id"]]
        trail = real_client.get(f"{URL}/{entry['id']}/audit", headers=LEAD)
        assert trail.status_code == 200
        last = trail.json()[-1]
        assert last["action"] == "status_changed"
        assert last["detail"] == f"Investigating -> Closed ({entry['closingReason']})"
        close_times.append(last["at"])
    # The list put the most recently closed first: its closing entry is not older than the next.
    assert close_times == sorted(close_times, reverse=True)


# --- refusals, in the contract's order: 401, 403, 422 ------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-token"}, EXPIRED],
    ids=["no token", "bad token", "expired token"],
)
def test_no_valid_token_is_401_even_without_status(real_client, app_database, headers):
    for params in ({"status": "Closed"}, {}):
        response = real_client.get(URL, params=params, headers=headers)
        assert (response.status_code, response.json()) == (401, UNAUTHORIZED_BODY)


@pytest.mark.parametrize("params", [{"status": "Closed"}, {"status": "Open"}, {"status": ""}, {}])
def test_an_analyst_is_403_whatever_the_status(real_client, app_database, params):
    response = real_client.get(URL, params=params, headers=ANALYST)
    assert (response.status_code, response.json()) == (403, FORBIDDEN_BODY)


@pytest.mark.parametrize(
    "params",
    [{"status": "New"}, {"status": "Investigating"}, {"status": "closed"}, {"status": ""}, {}],
)
def test_a_lead_with_another_empty_or_missing_status_is_422(real_client, app_database, params):
    response = real_client.get(URL, params=params, headers=LEAD)
    assert (response.status_code, response.json()) == (422, VALIDATION_BODY)


# --- read only ---------------------------------------------------------------------------------


def test_the_list_writes_nothing_and_adds_no_audit_entry(real_app, real_client, app_database):
    add_case(app_database, 1, status="Investigating")
    close(app_database, 1, 1)
    before = count_rows(app_database)
    commits = []
    event.listen(real_app.state.database.engine(), "commit", lambda conn: commits.append(1))
    closed(real_client)
    assert count_rows(app_database) == before
    assert commits == []


def test_the_list_is_one_select_for_any_number_of_cases(real_app, real_client, app_database):
    for case_id in range(1, 6):
        add_case(app_database, case_id, status="Investigating")
        close(app_database, case_id, case_id)
    statements = []
    event.listen(
        real_app.state.database.engine(),
        "before_cursor_execute",
        lambda conn, cursor, statement, *rest: statements.append(statement),
    )
    assert len(closed(real_client)) == 5
    # One SELECT for the list; the rest is the caller's own lookup (`current_user`).
    assert sum("FROM cases" in statement for statement in statements) == 1


# --- contract ----------------------------------------------------------------------------------


def test_the_route_matches_the_contract(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    contract_op = join_server_base(contract)[URL]["get"]
    generated_op = generated["paths"][URL]["get"]
    assert generated_op["operationId"] == contract_op["operationId"] == "getClosedCases"
    assert set(generated_op["responses"]) == set(contract_op["responses"])
    (parameter,) = generated_op["parameters"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == ("status", "query", True)
    # A one-value Literal is generated as `const`, the contract says `enum`; the same set.
    assert parameter["schema"].get("enum", [parameter["schema"].get("const")]) == ["Closed"]

    def schema(op, status):
        return op["responses"][status]["content"]["application/json"]["schema"]

    assert compare_schemas(contract, schema(contract_op, "200"), generated, schema(generated_op, "200")) == []
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    for status in ("401", "403", "422"):
        assert diff(api_error, canonical(generated, schema(generated_op, status))) == [], status
