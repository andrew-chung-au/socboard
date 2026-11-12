"""Login, `current_user`, the role checks and `GET /api/users` over HTTP (#13).

`real_app` is `create_app()` exactly; `app` adds the test-only routes of `backend_support.py`
(which use the real dependencies). Both run on a copy of the test database with the two test
users, and `SOCBOARD_TOKEN_SECRET` is set by `tests/conftest.py`.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest
from backend_support import (
    ANALYST_ID,
    ANALYST_PASSWORD,
    ANALYST_TOKEN,
    ANALYST_USERNAME,
    LEAD_ID,
    LEAD_PASSWORD,
    LEAD_TOKEN,
    LEAD_USERNAME,
    TEST_TOKEN_SECRET,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, select, update

from socboard import auth, database, passwords, tokens
from socboard.errors import INVALID_CREDENTIALS, UNAUTHORIZED, ApiHTTPException
from socboard.models import User as UserRow

OTHER_SECRET = "test-only-other-secret-fedcba9876543210"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == code
    return body


def assert_unauthorized(response):
    assert_error(response, 401, UNAUTHORIZED)
    assert response.headers["www-authenticate"] == "Bearer"


def run_sql(url, statement):
    engine = database.create_db_engine(url)
    try:
        with engine.begin() as conn:
            return conn.execute(statement)
    finally:
        engine.dispose()


def stored_hash(url, user_id):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                select(UserRow.password_hash).where(UserRow.id == user_id)
            ).scalar_one()
    finally:
        engine.dispose()


@pytest.fixture
def safe_client(real_app):
    """Returns a 500 as a response instead of raising, so a test can assert it never happens."""
    return TestClient(real_app, raise_server_exceptions=False)


# --- login -------------------------------------------------------------------------------------


def login(client, username, password):
    return client.post("/api/login", json={"username": username, "password": password})


def test_login_returns_a_token_and_the_user_without_a_hash(real_client):
    response = login(real_client, ANALYST_USERNAME, ANALYST_PASSWORD)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"token", "user"}
    assert body["user"] == {
        "id": str(ANALYST_ID),
        "username": ANALYST_USERNAME,
        "displayName": "Test Analyst",
        "role": "analyst",
    }
    assert "scrypt" not in response.text
    assert tokens.read_token(body["token"], TEST_TOKEN_SECRET, now=time.time()) == ANALYST_ID


def test_the_login_token_lasts_eight_hours(real_client):
    before = int(time.time())
    token = login(real_client, LEAD_USERNAME, LEAD_PASSWORD).json()["token"]
    after = int(time.time())
    payload = token.split(".")[0]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["sub"] == str(LEAD_ID)
    assert before + 8 * 3600 <= claims["exp"] <= after + 8 * 3600


def test_the_login_token_works_on_a_protected_route(real_client):
    token = login(real_client, LEAD_USERNAME, LEAD_PASSWORD).json()["token"]
    assert real_client.get("/api/users", headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("username", [f"  {ANALYST_USERNAME}", f"{ANALYST_USERNAME}\t", f" {ANALYST_USERNAME}\n"])
def test_the_username_is_trimmed_before_lookup(real_client, username):
    assert login(real_client, username, ANALYST_PASSWORD).status_code == 200


@pytest.mark.parametrize("username", [ANALYST_USERNAME.upper(), "Test-Analyst", "test-analys", "test-analyst-"])
def test_the_username_is_matched_exactly_after_trimming(real_client, username):
    assert_error(login(real_client, username, ANALYST_PASSWORD), 401, INVALID_CREDENTIALS)


@pytest.mark.parametrize(
    "password", [f" {ANALYST_PASSWORD}", f"{ANALYST_PASSWORD} ", ANALYST_PASSWORD.upper(), ""]
)
def test_the_password_is_used_as_sent(real_client, password):
    assert_error(login(real_client, ANALYST_USERNAME, password), 401, INVALID_CREDENTIALS)


def test_wrong_password_and_unknown_username_get_the_same_401(real_client):
    wrong = login(real_client, ANALYST_USERNAME, "test-only-wrong-pw")
    unknown = login(real_client, "test-nobody", ANALYST_PASSWORD)
    for response in (wrong, unknown):
        assert_error(response, 401, INVALID_CREDENTIALS)
    assert wrong.content == unknown.content
    assert wrong.json() == {"code": "invalid_credentials", "message": "Invalid username or password."}
    assert dict(wrong.headers) == dict(unknown.headers)


def test_both_refused_paths_run_the_password_verify(real_client, app_database, monkeypatch):
    calls = []
    real_verify = passwords.verify_password

    def spy(password, stored):
        calls.append(stored)
        return real_verify(password, stored)

    monkeypatch.setattr(passwords, "verify_password", spy)
    login(real_client, "test-nobody", "test-only-wrong-pw")
    assert calls == [passwords.DUMMY_HASH]
    calls.clear()
    login(real_client, ANALYST_USERNAME, "test-only-wrong-pw")
    assert calls == [stored_hash(app_database, ANALYST_ID)]


@pytest.mark.parametrize(
    "body",
    [
        {"password": "test-only-pw-000"},
        {"username": ANALYST_USERNAME},
        {},
        {"username": 1, "password": "test-only-pw-000"},
        {"username": ANALYST_USERNAME, "password": 12345678901234},
        {"username": None, "password": "test-only-pw-000"},
        {"username": [ANALYST_USERNAME], "password": "test-only-pw-000"},
        [ANALYST_USERNAME, ANALYST_PASSWORD],
        "text",
    ],
)
def test_a_login_body_that_fails_the_schema_is_422(real_client, body):
    assert_error(real_client.post("/api/login", json=body), 422, "validation_error")


def test_a_login_body_that_is_not_json_is_422(real_client):
    response = real_client.post(
        "/api/login", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert_error(response, 422, "validation_error")


@pytest.mark.parametrize(
    "content",
    [
        '{"username": "\\ud800", "password": "\\ud800"}',
        '{"username": "test-analyst", "password": "\\udc80-test-only"}',
    ],
)
def test_a_lone_surrogate_in_the_login_body_is_a_401_not_a_500(safe_client, content):
    # JSON can escape a lone surrogate, which strict UTF-8 cannot encode for the driver or scrypt.
    response = safe_client.post(
        "/api/login", content=content, headers={"Content-Type": "application/json"}
    )
    assert_error(response, 401, INVALID_CREDENTIALS)


# --- current_user: every refusal is 401 unauthorized, never a 500 ------------------------------


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def signed(claims, secret=TEST_TOKEN_SECRET):
    raw = claims if isinstance(claims, bytes) else json.dumps(claims).encode()
    payload = b64(raw)
    mac = hmac.new(secret.encode(), payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{b64(mac)}"


def _future():
    return int(time.time()) + 3600


def _payload_changed_under_old_signature():
    _, signature = ANALYST_TOKEN.split(".")
    payload = b64(json.dumps({"sub": str(LEAD_ID), "exp": _future()}).encode())
    return f"{payload}.{signature}"


REFUSED_HEADERS = {
    "no Authorization header": None,
    "Basic scheme": f"Basic {ANALYST_TOKEN}",
    "Token scheme": f"Token {ANALYST_TOKEN}",
    "token with no scheme": ANALYST_TOKEN,
    "Bearer with no token": "Bearer",
    "Bearer and a space": "Bearer ",
    "token without a dot": "Bearer " + ANALYST_TOKEN.replace(".", ""),
    "token with an extra dot": "Bearer " + ANALYST_TOKEN + ".x",
    "token with two dots inside": "Bearer a.b.c",
    "character outside base64url": "Bearer " + ANALYST_TOKEN.replace(".", "+."),
    "padding character": "Bearer " + ANALYST_TOKEN.replace(".", "=."),
    "two tokens": f"Bearer {ANALYST_TOKEN} {LEAD_TOKEN}",
    "payload not json": "Bearer " + signed(b"not json"),
    "payload a json list": "Bearer " + signed([str(ANALYST_ID)]),
    "sub refused by from_public_id": "Bearer " + signed({"sub": "01", "exp": _future()}),
    "sub an integer": "Bearer " + signed({"sub": ANALYST_ID, "exp": _future()}),
    "exp missing": "Bearer " + signed({"sub": str(ANALYST_ID)}),
    "exp not numeric": "Bearer " + signed({"sub": str(ANALYST_ID), "exp": "later"}),
    "exp a float": "Bearer " + signed({"sub": str(ANALYST_ID), "exp": float(_future())}),
    "bad signature": "Bearer " + ANALYST_TOKEN.split(".")[0] + "." + b64(b"\0" * 32),
    "payload changed under its old signature": "Bearer " + _payload_changed_under_old_signature(),
    "signed with another secret": "Bearer " + tokens.create_token(ANALYST_ID, OTHER_SECRET, time.time()),
    "at its exp": "Bearer "
    + tokens.create_token(ANALYST_ID, TEST_TOKEN_SECRET, time.time() - tokens.TOKEN_LIFETIME_SECONDS),
    "past its exp": "Bearer "
    + tokens.create_token(ANALYST_ID, TEST_TOKEN_SECRET, time.time() - tokens.TOKEN_LIFETIME_SECONDS - 60),
    "user id that does not exist": "Bearer " + tokens.create_token(999, TEST_TOKEN_SECRET, time.time()),
}


@pytest.mark.parametrize("name", sorted(REFUSED_HEADERS))
def test_current_user_refuses_with_401(safe_client, name):
    value = REFUSED_HEADERS[name]
    headers = {} if value is None else {"Authorization": value}
    assert_unauthorized(safe_client.get("/api/users", headers=headers))


def test_the_bearer_scheme_name_is_case_insensitive(real_client):
    # RFC 7235: the scheme is case-insensitive; FastAPI's HTTPBearer reads it that way.
    response = real_client.get("/api/users", headers={"Authorization": f"bearer {ANALYST_TOKEN}"})
    assert response.status_code == 200


def test_a_token_for_a_removed_user_is_refused(real_client, app_database):
    run_sql(
        app_database,
        insert(UserRow.__table__).values(
            id=3, username="test-temp", display_name="Temp", role="analyst", password_hash="x"
        ),
    )
    token = tokens.create_token(3, TEST_TOKEN_SECRET, now=time.time())
    assert real_client.get("/api/users", headers=bearer(token)).status_code == 200
    run_sql(app_database, delete(UserRow.__table__).where(UserRow.__table__.c.id == 3))
    assert_unauthorized(real_client.get("/api/users", headers=bearer(token)))


# --- the role comes from the database, on every request ---------------------------------------


def test_require_lead_answers_403_for_an_analyst_and_lets_a_lead_through(client):
    analyst = client.get("/api/cases", params={"status": "Closed"}, headers=bearer(ANALYST_TOKEN))
    assert_error(analyst, 403, "forbidden")
    lead = client.get("/api/cases", params={"status": "Closed"}, headers=bearer(LEAD_TOKEN))
    assert lead.status_code == 200


def test_the_role_is_read_from_the_database_not_the_token(client, app_database):
    url = "/api/cases"
    params = {"status": "Closed"}
    assert client.get(url, params=params, headers=bearer(ANALYST_TOKEN)).status_code == 403
    run_sql(app_database, update(UserRow.__table__).where(UserRow.__table__.c.id == ANALYST_ID).values(role="lead"))
    # The same token, unchanged: the new role applies at once.
    assert client.get(url, params=params, headers=bearer(ANALYST_TOKEN)).status_code == 200
    run_sql(app_database, update(UserRow.__table__).where(UserRow.__table__.c.id == ANALYST_ID).values(role="analyst"))
    assert client.get(url, params=params, headers=bearer(ANALYST_TOKEN)).status_code == 403


def test_the_token_payload_holds_no_role():
    payload = ANALYST_TOKEN.split(".")[0]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert set(claims) == {"sub", "exp"}


def test_ensure_lead_raises_the_same_403():
    with pytest.raises(ApiHTTPException) as error:
        auth.ensure_lead(auth.CurrentUser(id=ANALYST_ID, role="analyst"))
    assert (error.value.status_code, error.value.code) == (403, "forbidden")
    assert auth.ensure_lead(auth.CurrentUser(id=LEAD_ID, role="lead")) is None


# --- GET /api/users ----------------------------------------------------------------------------


@pytest.mark.parametrize("token", [ANALYST_TOKEN, LEAD_TOKEN])
def test_users_lists_every_user_in_id_order_without_a_hash(real_client, token):
    response = real_client.get("/api/users", headers=bearer(token))
    assert response.status_code == 200
    users = response.json()
    assert users == [
        {"id": "1", "username": ANALYST_USERNAME, "displayName": "Test Analyst", "role": "analyst"},
        {"id": "2", "username": LEAD_USERNAME, "displayName": "Test Lead", "role": "lead"},
    ]
    for user in users:
        assert set(user) == {"id", "username", "displayName", "role"}
    assert "scrypt" not in response.text
    assert "hash" not in response.text.lower()


def test_users_are_in_ascending_id_order(real_client, app_database):
    for user_id in (10, 5):
        run_sql(
            app_database,
            insert(UserRow.__table__).values(
                id=user_id, username=f"test-u{user_id}", display_name="U", role="analyst", password_hash="x"
            ),
        )
    ids = [user["id"] for user in real_client.get("/api/users", headers=bearer(LEAD_TOKEN)).json()]
    assert ids == ["1", "2", "5", "10"]


# --- every route but login, health and ingest needs a token ------------------------------------

OPEN_ROUTES = {("POST", "/api/login"), ("GET", "/api/health"), ("POST", "/api/alerts")}


def _every_route(app):
    """(method, path format) for every route the app serves, included routers flattened.

    FastAPI 0.162 keeps an included router as one wrapper entry in `app.routes`;
    `iter_route_contexts` yields the routes inside it with their full paths.
    """
    from fastapi.routing import iter_route_contexts

    found = set()
    for context in iter_route_contexts(app.routes):
        for method in context.methods or ():
            if method != "HEAD":
                found.add((method, context.path_format))
    return found


def test_the_route_walk_sees_every_documented_operation(real_app):
    walked = _every_route(real_app)
    documented = {
        (method.upper(), path)
        for path, item in real_app.openapi()["paths"].items()
        for method in item
    }
    assert documented <= walked
    assert {("POST", "/api/login"), ("GET", "/api/health"), ("GET", "/api/users")} <= walked


def test_every_route_except_login_health_and_ingest_needs_a_token(real_app):
    import re

    client = TestClient(real_app, raise_server_exceptions=False)
    checked = []
    for method, path_format in sorted(_every_route(real_app)):
        if (method, path_format) in OPEN_ROUTES:
            continue
        path = re.sub(r"\{[^}]+\}", "1", path_format)
        for headers in ({}, {"Authorization": "Bearer not-a-token"}):
            response = client.request(method, path, json={}, headers=headers)
            assert response.status_code == 401, (method, path_format)
            assert response.json()["code"] == UNAUTHORIZED, (method, path_format)
        checked.append((method, path_format))
    assert ("GET", "/api/users") in checked


def test_the_open_routes_answer_without_a_token(real_client):
    assert real_client.get("/api/health").status_code == 200
    assert login(real_client, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200


# --- the session per request -------------------------------------------------------------------


def test_the_session_rolls_back_what_a_route_did_not_commit(real_app, app_database):
    from fastapi import APIRouter, Depends

    from socboard.models import Asset
    from socboard.sessions import get_session

    router = APIRouter(prefix="/api")

    @router.post("/test-write")
    def write(commit: bool, session=Depends(get_session)) -> dict:
        session.add(Asset(name=f"test-asset-{commit}", criticality="Low"))
        session.flush()
        if commit:
            session.commit()
        return {}

    real_app.include_router(router)
    client = TestClient(real_app)
    assert client.post("/api/test-write", params={"commit": False}).status_code == 200
    assert client.post("/api/test-write", params={"commit": True}).status_code == 200
    engine = database.create_db_engine(app_database)
    try:
        with engine.connect() as conn:
            names = set(conn.execute(select(Asset.__table__.c.name)).scalars())
    finally:
        engine.dispose()
    assert "test-asset-True" in names
    assert "test-asset-False" not in names
