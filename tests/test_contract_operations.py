"""Which operations exist: the app against `openapi.yaml` (#22).

The app serves no schema (`openapi_url=None`), so the app's side is `create_app().openapi()`. The
contract's paths are joined to the `servers[0].url` base, and operations marked
`x-status: planned` are left out. Response sets are not compared here (the per-route contract
tests do that). The refused cases feed modified copies of the contract, made here, to the same
check, so it cannot pass whatever it is given.
"""

import copy

from backend_support import load_contract
from openapi_compare import join_server_base

from socboard.app import create_app

METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def operations(paths, skip_planned):
    return {
        (method, path): operation.get("operationId")
        for path, item in paths.items()
        for method, operation in item.items()
        if method in METHODS and not (skip_planned and operation.get("x-status") == "planned")
    }


def operation_differences(app_schema, contract):
    """Every (method, path) on one side only, and every operationId that differs."""
    app_ops = operations(app_schema["paths"], skip_planned=False)
    contract_ops = operations(join_server_base(contract), skip_planned=True)
    found = [f"only in the app: {pair}" for pair in sorted(app_ops.keys() - contract_ops.keys())]
    found += [f"only in openapi.yaml: {pair}" for pair in sorted(contract_ops.keys() - app_ops.keys())]
    found += [
        f"operationId differs for {pair}: app {app_ops[pair]!r}, openapi.yaml {contract_ops[pair]!r}"
        for pair in sorted(app_ops.keys() & contract_ops.keys())
        if app_ops[pair] != contract_ops[pair]
    ]
    return found


def app_schema():
    return create_app({"SOCBOARD_TOKEN_SECRET": "test-only-contract-secret-0123456789abc"}).openapi()


def test_the_app_and_the_contract_have_the_same_operations():
    differences = operation_differences(app_schema(), load_contract())
    assert differences == [], "\n".join(differences)


def test_the_only_planned_operation_today_is_reassign_case():
    planned = [
        operation["operationId"]
        for item in load_contract()["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and operation.get("x-status") == "planned"
    ]
    assert planned == ["reassignCase"]


# --- the check refuses a contract that differs ---------------------------------------------------


def test_a_contract_with_one_path_added_is_reported():
    contract = load_contract()
    contract["paths"]["/extra"] = {"get": {"operationId": "getExtra", "responses": {}}}
    assert operation_differences(app_schema(), contract) == ["only in openapi.yaml: ('get', '/api/extra')"]


def test_a_contract_with_one_method_removed_is_reported():
    contract = load_contract()
    del contract["paths"]["/queue"]["get"]
    assert operation_differences(app_schema(), contract) == ["only in the app: ('get', '/api/queue')"]


def test_a_contract_with_an_operation_id_changed_is_reported():
    contract = load_contract()
    contract["paths"]["/cases/{id}/claim"]["post"]["operationId"] = "takeCase"
    assert operation_differences(app_schema(), contract) == [
        "operationId differs for ('post', '/api/cases/{id}/claim'): app 'claimCase', openapi.yaml 'takeCase'"
    ]


def test_a_planned_operation_that_lost_its_marker_is_reported():
    contract = load_contract()
    del contract["paths"]["/cases/{id}/reassign"]["post"]["x-status"]
    assert operation_differences(app_schema(), contract) == [
        "only in openapi.yaml: ('post', '/api/cases/{id}/reassign')"
    ]


def test_an_unmodified_contract_gives_no_difference():
    contract = load_contract()
    untouched = copy.deepcopy(contract)
    assert operation_differences(app_schema(), contract) == []
    assert contract == untouched
