"""The handlers' bodies against the contract's unreferenced shared responses.

`UnknownRoute`, `MethodNotAllowed` and `InternalError` in `openapi.yaml` are referenced by no
operation (by design), so the schema comparison cannot see them on any path. Each case here
triggers the failure, checks the status, validates the body against the `ApiError` schema the
component references, and checks `code` equals the code in the component's example. The message
wording is not compared: the contract calls the example messages illustrative.
"""

import pytest
from backend_support import load_contract
from jsonschema import Draft202012Validator

CASES = [
    ("UnknownRoute", 404, "get", "/api/no-such-route", False),
    ("MethodNotAllowed", 405, "delete", "/api/health", False),
    ("InternalError", 500, "get", "/api/boom", True),
]


def resolve_local_ref(document, ref):
    assert ref.startswith("#/"), ref
    node = document
    for part in ref[2:].split("/"):
        node = node[part]
    return node


@pytest.mark.parametrize(("component", "status", "method", "path", "crash"), CASES)
def test_handler_body_matches_the_contract_component(
    client, crash_client, component, status, method, path, crash
):
    contract = load_contract()
    response_component = contract["components"]["responses"][component]
    media = response_component["content"]["application/json"]
    schema = resolve_local_ref(contract, media["schema"]["$ref"])
    example_code = next(iter(media["examples"].values()))["value"]["code"]

    response = getattr(crash_client if crash else client, method)(path)

    assert response.status_code == status
    body = response.json()
    Draft202012Validator(schema).validate(body)
    assert set(body) == {"code", "message"}
    assert body["code"] == example_code


def test_the_three_components_exist_and_no_operation_references_them():
    contract = load_contract()
    names = {case[0] for case in CASES}
    assert names <= set(contract["components"]["responses"])
    for name in names:
        reference = f"#/components/responses/{name}"
        for path, item in contract["paths"].items():
            for operation in item.values():
                if not isinstance(operation, dict):
                    continue
                for response in operation.get("responses", {}).values():
                    assert response.get("$ref") != reference, (path, name)
