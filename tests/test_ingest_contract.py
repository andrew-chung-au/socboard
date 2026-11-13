"""`POST /api/alerts` against `openapi.yaml` (`ingestAlert`, #14), with tests/openapi_compare.py.

Compared with the contract: the request schema, the `CaseSummary` response (with `User`), the
response statuses (201, 401, 422 and no others) and the security (one API key in `X-API-Key`, no
bearer scheme). The 401 and 422 bodies are `ApiError`. The length limits are compared too:
`maxLength` is not ignored by the normaliser, so a limit in the code and not in the contract fails.
"""

import pytest
from backend_support import load_contract
from openapi_compare import (
    canonical,
    compare_schemas,
    diff,
    join_server_base,
    operation_security,
    scheme_signature,
)

PATH = "/api/alerts"


@pytest.fixture(scope="module")
def contract():
    return load_contract()


@pytest.fixture
def generated(real_app):
    return real_app.openapi()


def operations(contract, generated):
    return join_server_base(contract)[PATH]["post"], generated["paths"][PATH]["post"]


def json_schema(response_or_body):
    return response_or_body["content"]["application/json"]["schema"]


def test_the_request_schema_equals_the_contract(contract, generated):
    contract_op, generated_op = operations(contract, generated)
    contract_body = json_schema(contract_op["requestBody"])
    generated_body = json_schema(generated_op["requestBody"])
    assert compare_schemas(contract, contract_body, generated, generated_body) == []
    assert generated_op["requestBody"]["required"] is True
    # Stated directly as well, so the test does not depend on the normaliser alone.
    resolved = canonical(generated, generated_body)
    assert resolved["required"] == sorted(
        ["source", "type", "asset", "timestamp", "title", "description"]
    )
    limits = {name: prop.get("maxLength") for name, prop in resolved["properties"].items()}
    assert limits == {
        "source": 200,
        "type": 200,
        "asset": 200,
        "timestamp": None,
        "title": 200,
        "description": 10000,
    }
    assert resolved["properties"]["timestamp"]["format"] == "date-time"


def test_the_201_response_equals_the_contracts_case_summary(contract, generated):
    contract_op, generated_op = operations(contract, generated)
    contract_body = json_schema(contract_op["responses"]["201"])
    generated_body = json_schema(generated_op["responses"]["201"])
    assert compare_schemas(contract, contract_body, generated, generated_body) == []
    resolved = canonical(generated, generated_body)
    assert "closingReason" in resolved["required"] and "owner" in resolved["required"]


def test_the_response_statuses_are_201_401_and_422_only(contract, generated):
    contract_op, generated_op = operations(contract, generated)
    assert set(contract_op["responses"]) == {"201", "401", "422"}
    assert set(generated_op["responses"]) == {"201", "401", "422"}


def test_the_401_and_422_bodies_are_api_error(contract, generated):
    _, generated_op = operations(contract, generated)
    api_error = canonical(contract, contract["components"]["schemas"]["ApiError"])
    for status in ("401", "422"):
        schema = json_schema(generated_op["responses"][status])
        assert diff(api_error, canonical(generated, schema)) == [], status
    assert sorted(api_error["required"]) == ["code", "message"]
    assert list(api_error["properties"]) == ["code", "message"]


def test_the_security_is_the_api_key_header_and_no_bearer(contract, generated):
    contract_op, generated_op = operations(contract, generated)
    assert operation_security(generated, generated_op) == operation_security(contract, contract_op)
    schemes = generated["components"]["securitySchemes"]
    used = [name for requirement in generated_op["security"] for name in requirement]
    assert len(used) == 1
    assert scheme_signature(schemes[used[0]]) == ("apiKey", None, "header", "X-API-Key")
    # The scheme appears although the dependency's own missing-key answer is never used.
    assert generated_op["responses"].keys().isdisjoint({"403"})


def test_the_operation_id_and_status_match_the_contract(contract, generated):
    contract_op, generated_op = operations(contract, generated)
    assert generated_op["operationId"] == contract_op["operationId"] == "ingestAlert"
