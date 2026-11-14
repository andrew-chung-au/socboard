"""Simultaneous claims of one case leave exactly one owner, on SQLite and on Postgres (#194, D7).

Spec section 5: a case has at most one owner, on any supported database. Each round blocks every
request behind a third connection that holds the case row's write lock (it updates the row without
changing it), releases them together, and then reads the case and its audit trail. Holding the row
is what makes the claims overlap in the database; a barrier only starts the threads together.

The tests are not marked `sqlite_only`, so the Postgres test job runs them (#192). On Postgres the
round waits until every request is seen waiting on a lock (`pg_stat_activity`), so a round where the
requests did not overlap fails instead of passing quietly. SQLite has no such view, so there the
round waits a fixed time inside the 5 s busy timeout.
"""

import threading
import time

from backend_support import ANALYST_ID, ANALYST_TOKEN, LEAD_ID, LEAD_TOKEN
from db_target import is_postgres
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from test_queue_api import add_case, run

from socboard import database
from socboard.models import AuditEntry, Case

ANALYST = {"Authorization": f"Bearer {ANALYST_TOKEN}"}
LEAD = {"Authorization": f"Bearer {LEAD_TOKEN}"}
USER_OF = {ANALYST_TOKEN: ANALYST_ID, LEAD_TOKEN: LEAD_ID}
ALREADY_CLAIMED = {"code": "already_claimed", "message": "Case is already owned."}
CASE_CLOSED = {"code": "case_closed", "message": "Closed cases cannot be claimed."}

CLAIMERS = 8  # the issue's minimum; the app's pool (5 plus 10 overflow) holds them all at once
ROUNDS = 50
CLOSE_ROUNDS = 20
SQLITE_WAIT = 0.3  # seconds; the claims are waiting on SQLite's busy handler by then
BLOCKED_TIMEOUT = 10.0  # seconds a Postgres round waits for every request to be blocked
JOIN_TIMEOUT = 30.0


def claim(client, case_id, headers):
    return client.post(f"/api/cases/{case_id}/claim", headers=headers)


def close(client, case_id, headers):
    body = {"to": "Closed", "closingReason": "duplicate"}
    return client.post(f"/api/cases/{case_id}/transition", json=body, headers=headers)


def state(url, case_id):
    """(owner_id, status) of the case and all its audit entries as (actor_id, action), oldest first."""

    def work(conn):
        owner, status = conn.execute(select(Case.owner_id, Case.status).where(Case.id == case_id)).one()
        audit = conn.execute(
            select(AuditEntry.actor_id, AuditEntry.action)
            .where(AuditEntry.case_id == case_id)
            .order_by(AuditEntry.id)
        ).all()
        return owner, status, [tuple(row) for row in audit]

    return run(url, work)


def _blocked_requests(conn):
    return conn.execute(
        text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
    ).scalar_one()


def _activity(conn):
    """Every other session on this database: state, wait event and the start of its query."""
    rows = conn.execute(
        text(
            "SELECT state, wait_event_type, wait_event, left(query, 60) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() ORDER BY pid"
        )
    ).all()
    return [tuple(row) for row in rows]


def race(real_app, url, case_id, calls):
    """Run every call in `calls` (a function of a TestClient) at once behind the case's row lock.

    Returns the responses in the order of `calls`. Fails if a request does not return, or, on
    Postgres, if not every request was waiting on the lock before it was released.
    """
    responses = [None] * len(calls)
    errors = []
    barrier = threading.Barrier(len(calls) + 1, timeout=10)  # a failing thread can't hang the run

    def attempt(index, call):
        try:
            client = TestClient(real_app)  # each request gets its own session from get_session
            barrier.wait()
            responses[index] = call(client)
        except Exception as error:  # reported below, so a thread's failure is not lost
            errors.append(repr(error))

    threads = [threading.Thread(target=attempt, args=(i, call)) for i, call in enumerate(calls)]
    blocker = database.create_db_engine(url)
    # Autocommit, so each poll is its own transaction: inside one transaction Postgres keeps returning
    # the snapshot of pg_stat_activity it took at the first read, and the count would never move
    # (seen in CI on f8e7cd4: the count froze at 4 of 8, and at 0 of 2).
    watcher = (
        database.create_db_engine(url).execution_options(isolation_level="AUTOCOMMIT") if is_postgres() else None
    )
    try:
        with blocker.connect() as conn:
            conn.execute(update(Case).where(Case.id == case_id).values(status=Case.status))
            for thread in threads:
                thread.start()
            barrier.wait()
            if watcher is None:
                time.sleep(SQLITE_WAIT)
            else:
                deadline = time.monotonic() + BLOCKED_TIMEOUT
                with watcher.connect() as watch:
                    blocked = _blocked_requests(watch)
                    while blocked < len(calls) and time.monotonic() < deadline:
                        time.sleep(0.02)
                        blocked = _blocked_requests(watch)
                    activity = [] if blocked == len(calls) else _activity(watch)
                assert blocked == len(calls), (
                    f"case {case_id}: only {blocked} of {len(calls)} requests were blocked; "
                    f"sessions on this database: {activity}"
                )
            conn.rollback()
        for thread in threads:
            thread.join(timeout=JOIN_TIMEOUT)
    finally:
        blocker.dispose()
        if watcher is not None:
            watcher.dispose()
    assert not errors, errors
    hung = [i for i, thread in enumerate(threads) if thread.is_alive()]
    assert not hung, f"case {case_id}: requests {hung} did not return within {JOIN_TIMEOUT} s"
    return responses


def test_many_simultaneous_claims_leave_one_owner_and_one_claimed_entry_every_round(real_app, app_database):
    tokens = [ANALYST_TOKEN if i % 2 == 0 else LEAD_TOKEN for i in range(CLAIMERS)]
    for round_number in range(1, ROUNDS + 1):
        case_id = round_number
        add_case(app_database, case_id, status="New")
        calls = [lambda client, t=t: claim(client, case_id, {"Authorization": f"Bearer {t}"}) for t in tokens]

        responses = race(real_app, app_database, case_id, calls)

        statuses = [response.status_code for response in responses]
        where = f"round {round_number}: statuses {statuses}"
        assert sorted(statuses) == [200] + [409] * (CLAIMERS - 1), where
        winner = statuses.index(200)
        winner_id = USER_OF[tokens[winner]]
        assert responses[winner].json()["owner"]["id"] == str(winner_id), where
        for index, response in enumerate(responses):
            if index != winner:
                assert response.json() == ALREADY_CLAIMED, where
        after = state(app_database, case_id)
        assert after == (winner_id, "New", [(winner_id, "claimed")]), where

        # A loser leaves nothing behind, and the case stays the winner's for any later claim.
        client = TestClient(real_app)
        for headers in (ANALYST, LEAD):
            late = claim(client, case_id, headers)
            assert (late.status_code, late.json()) == (409, ALREADY_CLAIMED), where
        assert state(app_database, case_id) == after, where


def test_a_claim_racing_a_close_leaves_one_of_the_two_consistent_states(real_app, app_database):
    seen = set()
    for round_number in range(1, CLOSE_ROUNDS + 1):
        case_id = 1000 + round_number
        add_case(app_database, case_id, status="Investigating")
        calls = [
            lambda client: claim(client, case_id, ANALYST),
            lambda client: close(client, case_id, LEAD),
        ]

        claimed, closed = race(real_app, app_database, case_id, calls)

        where = f"round {round_number}: claim {claimed.status_code}, close {closed.status_code}"
        assert closed.status_code == 200, where
        owner, status, audit = state(app_database, case_id)
        assert status == "Closed", where
        if claimed.status_code == 200:  # order A: the claim went first
            assert claimed.json()["owner"]["id"] == str(ANALYST_ID), where
            assert owner == ANALYST_ID, where
            assert audit == [(ANALYST_ID, "claimed"), (LEAD_ID, "status_changed")], where
            seen.add("claim first")
        else:  # order B: the close went first
            assert (claimed.status_code, claimed.json()) == (409, CASE_CLOSED), where
            assert owner is None, where
            assert audit == [(LEAD_ID, "status_changed")], where
            seen.add("close first")
    assert seen  # which orders occurred is not fixed; each round is one of the two
