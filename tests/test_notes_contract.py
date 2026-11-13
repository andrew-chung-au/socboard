"""`POST /api/cases/{id}/notes` (`addNote`) against `openapi.yaml` (#18), with openapi_compare.py.

Compared: the request body (`NoteRequest`), the 201 body (`Note`), the 401, 404, 409 and 422 error
bodies, the response statuses, the security and the operation id. The 5,000-character limit is
not in the contract, so the generated `NoteRequest` must carry no length keyword (the limit is a
validator); a change that adds `maxLength` shows up here as a difference from the contract.
"""

import pytest
from backend_support import load_contract
from openapi_compare import (
    canonical,
    compare_schemas,
    diff,
    join_server_base,
    operation_security,
    resolve_pointer,
)

PATH = "/api/cases/{id}/notes"


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture
def generated(real_app):
    return real_app.openapi()


@pytest.fixture
def ops(contract, generated):
    return join_server_base(contract)[PATH]["post"], generated["paths"][PATH]["post"]


def response_schema(document, response):
    if "$ref" in response:
        response = resolve_pointer(document, response["$ref"])
    return response["content"]["application/json"]["schema"]


def body_schema(operation):
    assert operation["requestBody"]["required"] is True
    return operation["requestBody"]["content"]["application/json"]["schema"]


def test_the_request_body_equals_the_contract_and_has_no_length_keyword(contract, generated, ops):
    contract_op, generated_op = ops
    assert compare_schemas(contract, body_schema(contract_op), generated, body_schema(generated_op)) == []
    text = canonical(generated, body_schema(generated_op))["properties"]["text"]
    assert text == {"type": "string"}


def test_the_201_response_equals_the_contract(contract, generated, ops):
    contract_op, generated_op = ops
    contract_body = response_schema(contract, contract_op["responses"]["201"])
    generated_body = response_schema(generated, generated_op["responses"]["201"])
    assert compare_schemas(contract, contract_body, generated, generated_body) == []


def test_the_response_statuses_match_the_contract(ops):
    contract_op, generated_op = ops
    expected = {"201", "401", "404", "409", "422"}
    assert set(contract_op["responses"]) == set(generated_op["responses"]) == expected


@pytest.mark.parametrize("status", ["401", "404", "409", "422"])
def test_the_error_bodies_are_api_error(contract, generated, ops, status):
    _, generated_op = ops
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    schema = response_schema(generated, generated_op["responses"][status])
    assert diff(api_error, canonical(generated, schema)) == []


def test_the_security_is_the_bearer_token(contract, generated, ops):
    contract_op, generated_op = ops
    assert operation_security(generated, generated_op) == operation_security(contract, contract_op)
    assert operation_security(generated, generated_op) != []


def test_the_operation_id_and_the_path_parameter_match_the_contract(ops):
    contract_op, generated_op = ops
    assert generated_op["operationId"] == contract_op["operationId"] == "addNote"
    (parameter,) = generated_op["parameters"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == ("id", "path", True)
    assert parameter["schema"]["type"] == "string"
