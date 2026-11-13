"""`GET /api/queue` (`getQueue`) and `GET /api/cases/{id}` (`getCase`) against `openapi.yaml` (#15).

Compared with the contract, using tests/openapi_compare.py: the 200 schemas (`QueueResponse` with
`CaseSummary` and `User`; `CaseDetail` with `Note`), the 401 and 404 bodies, the security and the
operation ids.

One known difference: FastAPI documents a 422 for any route with a path parameter, and the
contract has none on `getCase`. The route declares the contract's `ApiError` 422 (so the generated
document has no `HTTPValidationError`), and a string id can never fail validation, so it is never
returned. The tests below name that difference and accept nothing else.
"""

import pytest
from backend_support import load_contract
from openapi_compare import canonical, compare_schemas, diff, join_server_base, operation_security

QUEUE = "/api/queue"
CASE = "/api/cases/{id}"


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture
def generated(real_app):
    return real_app.openapi()


def operations(contract, generated, path):
    return join_server_base(contract)[path]["get"], generated["paths"][path]["get"]


def json_schema(response):
    return response["content"]["application/json"]["schema"]


@pytest.mark.parametrize("path", [QUEUE, CASE])
def test_the_200_response_equals_the_contract(contract, generated, path):
    contract_op, generated_op = operations(contract, generated, path)
    contract_body = json_schema(contract_op["responses"]["200"])
    generated_body = json_schema(generated_op["responses"]["200"])
    assert compare_schemas(contract, contract_body, generated, generated_body) == []


def test_the_queue_and_detail_fields_are_stated_directly(contract, generated):
    queue = canonical(generated, json_schema(generated["paths"][QUEUE]["get"]["responses"]["200"]))
    assert queue["required"] == ["cases", "servedAt"]
    assert queue["properties"]["servedAt"]["format"] == "date-time"
    detail = canonical(generated, json_schema(generated["paths"][CASE]["get"]["responses"]["200"]))
    assert {"description", "severityRule", "notes", "owner", "closingReason"} <= set(detail["required"])
    assert detail["properties"]["notes"]["items"]["required"] == ["author", "createdAt", "id", "text"]


def test_the_response_statuses_match_the_contract(contract, generated):
    queue_contract, queue_generated = operations(contract, generated, QUEUE)
    assert set(queue_generated["responses"]) == set(queue_contract["responses"]) == {"200", "401"}
    case_contract, case_generated = operations(contract, generated, CASE)
    assert set(case_contract["responses"]) == {"200", "401", "404"}
    assert set(case_generated["responses"]) == {"200", "401", "404", "422"}


@pytest.mark.parametrize(("path", "status"), [(QUEUE, "401"), (CASE, "401"), (CASE, "404"), (CASE, "422")])
def test_the_error_bodies_are_api_error(contract, generated, path, status):
    _, generated_op = operations(contract, generated, path)
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    schema = json_schema(generated_op["responses"][status])
    assert diff(api_error, canonical(generated, schema)) == []


@pytest.mark.parametrize("path", [QUEUE, CASE])
def test_the_security_is_the_bearer_token(contract, generated, path):
    contract_op, generated_op = operations(contract, generated, path)
    assert operation_security(generated, generated_op) == operation_security(contract, contract_op)
    assert operation_security(generated, generated_op) != []


def test_the_operation_ids_and_the_path_parameter_match_the_contract(contract, generated):
    queue_contract, queue_generated = operations(contract, generated, QUEUE)
    case_contract, case_generated = operations(contract, generated, CASE)
    assert queue_generated["operationId"] == queue_contract["operationId"] == "getQueue"
    assert case_generated["operationId"] == case_contract["operationId"] == "getCase"
    (parameter,) = case_generated["parameters"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == ("id", "path", True)
    assert parameter["schema"]["type"] == "string"
    assert "parameters" not in queue_generated
