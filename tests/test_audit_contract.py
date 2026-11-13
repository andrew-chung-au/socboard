"""`GET /api/cases/{id}/audit` (`getAudit`) against `openapi.yaml` (#19), with openapi_compare.py.

Compared: the 200 body (an array of `AuditEntry`, with `User` and the actions), the 401, 403 and 404
error bodies, the response statuses, the security, the operation id and the Lead-only wording.

Two known differences, named here and accepted as nothing else:
- The contract writes the actor as `oneOf` (the user, or the string "system"); pydantic writes
  `anyOf`. For two non-null alternatives the comparison keeps the keyword, so the test changes the
  CONTRACT's `oneOf` to `anyOf` on a copy. Safe: the alternatives cannot both match (an object and
  a string), so `oneOf` and `anyOf` accept the same documents.
- FastAPI documents a 422 for a route with a path parameter and the contract has none (as for
  `getCase`, test_queue_contract.py). The route declares the contract's `ApiError` 422.
"""

import copy

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

PATH = "/api/cases/{id}/audit"


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture
def generated(real_app):
    return real_app.openapi()


@pytest.fixture
def same_keyword_contract(contract):
    """The contract with the actor's `oneOf` written as `anyOf` (see the module docstring)."""
    document = copy.deepcopy(contract)
    actor = document["components"]["schemas"]["AuditEntry"]["properties"]["actor"]
    actor["anyOf"] = actor.pop("oneOf")
    return document


def response_schema(document, response):
    if "$ref" in response:
        response = resolve_pointer(document, response["$ref"])
    return response["content"]["application/json"]["schema"]


def test_the_200_response_equals_the_contract(same_keyword_contract, generated):
    contract_op = join_server_base(same_keyword_contract)[PATH]["get"]
    generated_op = generated["paths"][PATH]["get"]
    contract_body = response_schema(same_keyword_contract, contract_op["responses"]["200"])
    generated_body = response_schema(generated, generated_op["responses"]["200"])
    assert compare_schemas(same_keyword_contract, contract_body, generated, generated_body) == []
    # Stated directly as well, so the test does not depend on the normaliser alone.
    entry = canonical(generated, generated_body)["items"]
    assert entry["required"] == ["action", "actor", "at", "detail", "id"]
    assert entry["properties"]["action"]["enum"] == sorted(
        ["ingested", "claimed", "status_changed", "note_added", "reassigned"]
    )
    assert {"const": "system", "type": "string"} in entry["properties"]["actor"]["anyOf"]


def test_the_response_statuses_are_the_contracts_plus_the_path_parameters_422(contract, generated):
    contract_op = join_server_base(contract)[PATH]["get"]
    generated_op = generated["paths"][PATH]["get"]
    assert set(contract_op["responses"]) == {"200", "401", "403", "404"}
    assert set(generated_op["responses"]) == {"200", "401", "403", "404", "422"}
    # The contract reuses its shared forbidden response.
    assert contract_op["responses"]["403"] == {"$ref": "#/components/responses/Forbidden"}


@pytest.mark.parametrize("status", ["401", "403", "404", "422"])
def test_the_error_bodies_are_api_error(contract, generated, status):
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    schema = response_schema(generated, generated["paths"][PATH]["get"]["responses"][status])
    assert diff(api_error, canonical(generated, schema)) == []


def test_the_security_is_the_bearer_token(contract, generated):
    contract_op = join_server_base(contract)[PATH]["get"]
    generated_op = generated["paths"][PATH]["get"]
    assert operation_security(generated, generated_op) == operation_security(contract, contract_op)
    assert operation_security(generated, generated_op) != []


def test_the_operation_id_and_the_path_parameter_match_the_contract(contract, generated):
    contract_op = join_server_base(contract)[PATH]["get"]
    generated_op = generated["paths"][PATH]["get"]
    assert generated_op["operationId"] == contract_op["operationId"] == "getAudit"
    (parameter,) = generated_op["parameters"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == ("id", "path", True)
    assert parameter["schema"]["type"] == "string"


def test_the_contract_says_lead_only_with_the_order_401_403_404(contract):
    description = contract["paths"]["/cases/{id}/audit"]["get"]["description"]
    assert "Lead-only" in description
    assert "Order: 401, 403, 404." in description
    assert "any authenticated user" not in description
