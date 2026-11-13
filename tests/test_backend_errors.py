"""The error shape: every failure is exactly `{ code, message }` (openapi.yaml, #9)."""

import asyncio
import json

from backend_support import ANALYST_TOKEN, KNOWN_CASE_ID, MARKER
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from socboard import errors


def assert_api_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == code
    assert isinstance(body["message"], str) and body["message"]
    assert "detail" not in body
    assert "status" not in body
    return body


def analyst():
    return {"Authorization": f"Bearer {ANALYST_TOKEN}"}


# --- the three handlers are the ones installed ------------------------------------------------


def test_the_three_custom_handlers_are_installed(app):
    assert app.exception_handlers[StarletteHTTPException] is errors.http_exception_handler
    assert app.exception_handlers[RequestValidationError] is errors.validation_exception_handler
    assert app.exception_handlers[Exception] is errors.unexpected_exception_handler


# --- handler 1: HTTP exceptions ---------------------------------------------------------------


def test_unknown_route_is_404_not_found(client):
    assert_api_error(client.get("/api/no-such-route"), 404, "not_found")


def test_unknown_route_outside_the_api_prefix_is_404_not_found(client):
    assert_api_error(client.get("/health"), 404, "not_found")


def test_wrong_method_is_405_method_not_allowed_with_allow_header(client):
    response = client.delete("/api/health")
    assert_api_error(response, 405, "method_not_allowed")
    allowed = [method.strip() for method in response.headers["allow"].split(",")]
    assert "GET" in allowed


def test_raised_http_exception_uses_the_shape_and_hides_its_detail(client):
    response = client.get("/api/raise-http")
    assert_api_error(response, 403, "forbidden")
    assert MARKER not in response.text


def test_raised_http_exception_headers_are_passed_through(client):
    response = client.get("/api/raise-http-headers")
    assert_api_error(response, 404, "not_found")
    assert response.headers["x-test-header"] == "kept"


def test_api_http_exception_carries_its_own_code_and_message(client):
    body = assert_api_error(client.get("/api/raise-api"), 409, "already_claimed")
    assert body["message"] == "Case is already owned."


def test_http_exception_without_a_contract_code_is_a_generic_500(client):
    response = client.get("/api/raise-http-unmapped")
    body = assert_api_error(response, 500, "internal_error")
    assert "teapot" not in body["message"]


# --- handler 2: request validation ------------------------------------------------------------


def test_validation_failure_is_422_validation_error(client):
    response = client.post(
        f"/api/cases/{KNOWN_CASE_ID}/transition", json={"nope": 1}, headers=analyst()
    )
    body = assert_api_error(response, 422, "validation_error")
    assert body["message"] == errors.VALIDATION_MESSAGE


def test_validation_body_is_fixed_whatever_was_wrong(client):
    first = client.get("/api/items", params={"limit": "x"})
    second = client.get("/api/items/not-a-number")
    assert first.json() == second.json()


# --- no submitted value reaches a client ------------------------------------------------------


def test_marker_in_an_invalid_body_field_is_not_echoed(client):
    response = client.post(
        f"/api/cases/{KNOWN_CASE_ID}/transition", json={"to": MARKER}, headers=analyst()
    )
    assert_api_error(response, 422, "validation_error")
    assert MARKER not in response.text


def test_marker_in_an_invalid_path_parameter_is_not_echoed(client):
    response = client.get(f"/api/items/{MARKER}")
    assert_api_error(response, 422, "validation_error")
    assert MARKER not in response.text


def test_marker_in_an_invalid_query_parameter_is_not_echoed(client):
    response = client.get("/api/items", params={"limit": MARKER})
    assert_api_error(response, 422, "validation_error")
    assert MARKER not in response.text


def test_marker_in_an_unknown_path_is_not_echoed_in_the_404(client):
    response = client.get(f"/api/no-such-{MARKER}")
    assert_api_error(response, 404, "not_found")
    assert MARKER not in response.text


def test_marker_in_an_unknown_case_id_is_not_echoed_in_the_404(client):
    response = client.post(
        f"/api/cases/{MARKER}/transition", json={"to": "Investigating"}, headers=analyst()
    )
    assert_api_error(response, 404, "not_found")
    assert MARKER not in response.text


# --- handler 3: unexpected exceptions ---------------------------------------------------------


def test_unexpected_exception_is_a_generic_500_without_internals(crash_client):
    response = crash_client.get("/api/boom")
    body = assert_api_error(response, 500, "internal_error")
    assert body["message"] == errors.INTERNAL_MESSAGE
    # The generic message does not contain the exception text, a path or a trace.
    for leaked in ("secret-internal-detail", "secret_module", "/srv/app", "Traceback", ".py"):
        assert leaked not in response.text


# --- an unparseable body is mapped once, in the shared handler (#102) -------------------------


def test_unparseable_body_on_a_route_other_than_login_is_the_same_422(client):
    # Bytes that are not UTF-8: FastAPI raises HTTPException(400) for this, not a validation error.
    response = client.post(
        f"/api/cases/{KNOWN_CASE_ID}/transition",
        content=b'{"to": "\xff", "note": "' + MARKER.encode() + b'"}',
        headers={**analyst(), "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"code": "validation_error", "message": errors.VALIDATION_MESSAGE}
    assert MARKER not in response.text


def test_only_the_body_parsing_400_is_mapped_to_422():
    # Another 400 has no contract code, so it stays a generic 500 (like the 418 test above).
    other = StarletteHTTPException(status_code=400, detail="Bad thing")
    response = asyncio.run(errors.http_exception_handler(None, other))
    assert response.status_code == 500
    assert json.loads(response.body) == {
        "code": "internal_error",
        "message": errors.INTERNAL_MESSAGE,
    }
