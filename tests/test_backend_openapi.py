"""Generated schema against `openapi.yaml`, for what #10 defines only.

What #10 defines and compares: the `ApiError` component (fields `code` and `message`, both
required strings, no `status`), and that the generated document answers 422 with `ApiError`
(not FastAPI's `HTTPValidationError`) on the routes that take input. The health route is in the
contract (`GET /health`, #74) and `test_health_route_equals_the_contract` compares its 200 schema,
security and response statuses with the generated document. Business-operation schemas are compared by the endpoint issues (#14 on),
using tests/openapi_compare.py.

Normalisations applied by tests/openapi_compare.py (what differs, which side changes, why safe):

- Server base. The contract's paths are relative to `servers[0].url` (`/api`); FastAPI's paths
  carry the prefix from the router. The CONTRACT side changes: `join_server_base` puts `/api`
  in front of its paths. Safe: the contract says all paths are under that base.
- Nullable fields. The contract writes `oneOf: [X, {type: "null"}]`, pydantic writes `anyOf`.
  Both sides change to `{"nullable": X}`, only when there is exactly one non-null member plus
  null. Any other `oneOf`/`anyOf` keeps its keyword, so a non-null `oneOf` against `anyOf` is
  reported as different.
- `CaseDetail`. The contract writes `allOf` of `CaseSummary` and an object; FastAPI emits one
  flat object. Both sides are flattened (properties merged, required unioned). Safe: `allOf` of
  object schemas without `additionalProperties: false` accepts exactly the merged object.
- `Id` and other `$ref`s. The contract shares `Id` by `$ref`, pydantic inlines `string`. Both
  sides are resolved and inlined. `title`, `description`, `examples`, `example`, `minLength`,
  `default` and `summary` are dropped on both sides: FastAPI adds `title` and `default`, the
  contract adds prose and keeps `minLength` on opaque ids. Safe: they do not change structure
  (fields, required, types) which is what this comparison checks.
- Security scheme names. The contract uses `bearerAuth` and `ingestKey`, FastAPI names a scheme
  after its dependency class. Schemes are compared by `type`, `scheme`, `in` and `name`, and an
  operation is mapped to schemes through that signature. Safe: the key name is only a label. The
  real app is not compared for security in #10 (its test-only dependencies read the header and
  declare no scheme); #13 and #14 add that.

A normaliser that compares nothing would pass whatever it is given, so the unit tests below feed
it fragments read from `openapi.yaml` at test time against hand-built FastAPI-style equivalents,
and require a mismatched fragment (a missing required field, a changed type) to be reported.
"""

import copy
import json

import pytest
from backend_support import load_contract
from openapi_compare import (
    canonical,
    compare_schemas,
    diff,
    join_server_base,
    operation_security,
    resolve_pointer,
    scheme_signature,
)


@pytest.fixture(scope="module")
def contract():
    return load_contract()


def contract_schema(contract, name):
    return contract["components"]["schemas"][name]


# --- hand-built FastAPI-style fragments (titles, inlined Id, anyOf, flat CaseDetail) -----------

ID_FASTAPI = {"type": "string", "title": "Id"}

USER_FASTAPI = {
    "type": "object",
    "title": "User",
    "required": ["id", "username", "displayName", "role"],
    "properties": {
        "id": {"type": "string", "title": "Id"},
        "username": {"type": "string", "title": "Username"},
        "displayName": {"type": "string", "title": "Displayname"},
        "role": {"type": "string", "enum": ["analyst", "lead"], "title": "Role"},
    },
}

CASE_SUMMARY_FASTAPI = {
    "type": "object",
    "title": "CaseSummary",
    "required": [
        "id",
        "title",
        "alertType",
        "source",
        "asset",
        "assetCriticality",
        "severity",
        "status",
        "owner",
        "alertTimestamp",
        "ingestedAt",
        "closingReason",
    ],
    "properties": {
        "id": {"type": "string", "title": "Id"},
        "title": {"type": "string", "title": "Title"},
        "alertType": {"type": "string", "title": "Alerttype"},
        "source": {"type": "string", "title": "Source"},
        "asset": {"type": "string", "title": "Asset"},
        "assetCriticality": {
            "type": "string",
            "enum": ["Low", "Medium", "High", "Critical"],
            "title": "Assetcriticality",
        },
        "severity": {
            "type": "string",
            "enum": ["Low", "Medium", "High", "Critical"],
            "title": "Severity",
        },
        "status": {
            "type": "string",
            "enum": ["New", "Investigating", "Closed"],
            "title": "Status",
        },
        "owner": {"anyOf": [{"$ref": "#/components/schemas/User"}, {"type": "null"}]},
        "alertTimestamp": {"type": "string", "format": "date-time", "title": "Alerttimestamp"},
        "ingestedAt": {"type": "string", "format": "date-time", "title": "Ingestedat"},
        "closingReason": {
            "anyOf": [
                {"type": "string", "enum": ["true_positive", "false_positive", "duplicate"]},
                {"type": "null"},
            ],
            "title": "Closingreason",
        },
    },
}

NOTE_FASTAPI = {
    "type": "object",
    "title": "Note",
    "required": ["id", "author", "text", "createdAt"],
    "properties": {
        "id": {"type": "string", "title": "Id"},
        "author": {"$ref": "#/components/schemas/User"},
        "text": {"type": "string", "title": "Text"},
        "createdAt": {"type": "string", "format": "date-time", "title": "Createdat"},
    },
}


def case_detail_fastapi():
    flat = copy.deepcopy(CASE_SUMMARY_FASTAPI)
    flat["title"] = "CaseDetail"
    flat["required"] = flat["required"] + ["description", "severityRule", "notes"]
    flat["properties"]["description"] = {"type": "string", "title": "Description"}
    flat["properties"]["severityRule"] = {"type": "string", "title": "Severityrule"}
    flat["properties"]["notes"] = {
        "type": "array",
        "items": {"$ref": "#/components/schemas/Note"},
        "title": "Notes",
    }
    return flat


def fastapi_style_doc():
    return {
        "openapi": "3.1.0",
        "paths": {},
        "components": {
            "schemas": {
                "User": copy.deepcopy(USER_FASTAPI),
                "Note": copy.deepcopy(NOTE_FASTAPI),
                "CaseSummary": copy.deepcopy(CASE_SUMMARY_FASTAPI),
                "CaseDetail": case_detail_fastapi(),
            },
            "securitySchemes": {
                "HTTPBearer": {"type": "http", "scheme": "bearer"},
                "APIKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            },
        },
    }


# --- unit tests of the normaliser, on fragments of the real contract ---------------------------


def test_id_ref_equals_inlined_string(contract):
    fastapi_doc = fastapi_style_doc()
    assert compare_schemas(contract, contract_schema(contract, "Id"), fastapi_doc, ID_FASTAPI) == []


def test_id_changed_type_is_reported(contract):
    assert compare_schemas(
        contract, contract_schema(contract, "Id"), {}, {"type": "integer", "title": "Id"}
    )


def test_case_summary_owner_oneof_null_equals_anyof_null(contract):
    owner = contract_schema(contract, "CaseSummary")["properties"]["owner"]
    fastapi_doc = fastapi_style_doc()
    fastapi_owner = fastapi_doc["components"]["schemas"]["CaseSummary"]["properties"]["owner"]
    assert compare_schemas(contract, owner, fastapi_doc, fastapi_owner) == []


def test_owner_missing_user_field_is_reported(contract):
    owner = contract_schema(contract, "CaseSummary")["properties"]["owner"]
    fastapi_doc = fastapi_style_doc()
    fastapi_doc["components"]["schemas"]["User"]["required"].remove("role")
    fastapi_owner = fastapi_doc["components"]["schemas"]["CaseSummary"]["properties"]["owner"]
    assert compare_schemas(contract, owner, fastapi_doc, fastapi_owner)


def test_owner_changed_field_type_is_reported(contract):
    owner = contract_schema(contract, "CaseSummary")["properties"]["owner"]
    fastapi_doc = fastapi_style_doc()
    fastapi_doc["components"]["schemas"]["User"]["properties"]["username"]["type"] = "integer"
    fastapi_owner = fastapi_doc["components"]["schemas"]["CaseSummary"]["properties"]["owner"]
    assert compare_schemas(contract, owner, fastapi_doc, fastapi_owner)


def test_owner_that_is_not_nullable_is_reported(contract):
    owner = contract_schema(contract, "CaseSummary")["properties"]["owner"]
    fastapi_doc = fastapi_style_doc()
    assert compare_schemas(contract, owner, fastapi_doc, {"$ref": "#/components/schemas/User"})


def test_oneof_against_anyof_of_non_null_alternatives_is_reported():
    document = {}
    left = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    right = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert compare_schemas(document, left, document, right)
    assert compare_schemas(document, left, document, left) == []


def test_case_detail_allof_equals_flat_object(contract):
    detail = contract_schema(contract, "CaseDetail")
    fastapi_doc = fastapi_style_doc()
    flat = fastapi_doc["components"]["schemas"]["CaseDetail"]
    assert compare_schemas(contract, detail, fastapi_doc, flat) == []


def test_case_detail_missing_required_field_is_reported(contract):
    detail = contract_schema(contract, "CaseDetail")
    fastapi_doc = fastapi_style_doc()
    fastapi_doc["components"]["schemas"]["CaseDetail"]["required"].remove("notes")
    assert compare_schemas(
        contract, detail, fastapi_doc, fastapi_doc["components"]["schemas"]["CaseDetail"]
    )


def test_case_detail_changed_type_is_reported(contract):
    detail = contract_schema(contract, "CaseDetail")
    fastapi_doc = fastapi_style_doc()
    flat = fastapi_doc["components"]["schemas"]["CaseDetail"]
    flat["properties"]["description"]["type"] = "integer"
    assert compare_schemas(contract, detail, fastapi_doc, flat)


def test_case_detail_missing_property_is_reported(contract):
    detail = contract_schema(contract, "CaseDetail")
    fastapi_doc = fastapi_style_doc()
    flat = fastapi_doc["components"]["schemas"]["CaseDetail"]
    del flat["properties"]["severityRule"]
    assert compare_schemas(contract, detail, fastapi_doc, flat)


def test_normaliser_does_not_pass_everything():
    one = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}
    other = {"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}}
    assert compare_schemas({}, one, {}, other)
    assert diff(canonical({}, one), canonical({}, one)) == []


def test_allof_members_that_disagree_raise():
    with pytest.raises(ValueError):
        canonical({}, {"allOf": [{"type": "object"}, {"type": "array"}]})


def test_security_schemes_compare_by_signature_not_key_name(contract):
    schemes = contract["components"]["securitySchemes"]
    fastapi_schemes = fastapi_style_doc()["components"]["securitySchemes"]
    assert scheme_signature(schemes["bearerAuth"]) == scheme_signature(fastapi_schemes["HTTPBearer"])
    assert scheme_signature(schemes["ingestKey"]) == scheme_signature(fastapi_schemes["APIKeyHeader"])
    assert scheme_signature(schemes["bearerAuth"]) != scheme_signature(
        fastapi_schemes["APIKeyHeader"]
    )


def test_operation_security_maps_operations_through_the_signature(contract):
    fastapi_doc = fastapi_style_doc()
    queue = contract["paths"]["/queue"]["get"]  # inherits the document's bearerAuth
    ingest = contract["paths"]["/alerts"]["post"]  # ingestKey only
    login = contract["paths"]["/login"]["post"]  # security: []
    fastapi_bearer = {"security": [{"HTTPBearer": []}]}
    fastapi_key = {"security": [{"APIKeyHeader": []}]}
    assert operation_security(contract, queue) == operation_security(fastapi_doc, fastapi_bearer)
    assert operation_security(contract, ingest) == operation_security(fastapi_doc, fastapi_key)
    assert operation_security(contract, login) == []
    assert operation_security(fastapi_doc, {}) == []
    # A mismatch is reported: the ingest operation is not the bearer scheme.
    assert operation_security(contract, ingest) != operation_security(fastapi_doc, fastapi_bearer)
    assert operation_security(contract, queue) != operation_security(fastapi_doc, fastapi_key)


def test_server_base_is_joined_to_the_contract_paths(contract):
    joined = join_server_base(contract)
    assert "/api/cases" in joined
    assert "/api/cases/{id}/transition" in joined
    assert "/cases" not in joined


# --- the real app ------------------------------------------------------------------------------


def test_generated_api_error_equals_the_contract(app, contract):
    generated = app.openapi()
    api_error = generated["components"]["schemas"]["ApiError"]
    assert compare_schemas(
        contract, contract_schema(contract, "ApiError"), generated, api_error
    ) == []
    # Stated directly as well, so the test does not depend on the normaliser alone.
    assert sorted(api_error["required"]) == ["code", "message"]
    assert api_error["properties"]["code"]["type"] == "string"
    assert api_error["properties"]["message"]["type"] == "string"
    assert "status" not in api_error["properties"]


def test_routes_with_input_answer_422_with_api_error(app, contract):
    generated = app.openapi()
    expected = canonical(contract, contract_schema(contract, "ApiError"))
    checked = 0
    for path, item in generated["paths"].items():
        if path == "/api/health":
            continue  # no parameters and no body, so no 422
        for operation in item.values():
            if not (operation.get("parameters") or operation.get("requestBody")):
                continue
            response = operation["responses"]["422"]
            schema = response["content"]["application/json"]["schema"]
            assert diff(expected, canonical(generated, schema)) == [], path
            checked += 1
    assert checked >= 3  # the test-only routes with a body, a path or a query parameter


def test_contract_routes_defined_here_declare_the_contracts_422(app, contract):
    generated = app.openapi()
    contract_paths = join_server_base(contract)
    api_error = canonical(contract, contract_schema(contract, "ApiError"))
    for path, method in (("/api/cases", "get"), ("/api/cases/{id}/transition", "post")):
        contract_422 = contract_paths[path][method]["responses"]["422"]
        if "$ref" in contract_422:
            contract_422 = resolve_pointer(contract, contract_422["$ref"])
        contract_schema_422 = contract_422["content"]["application/json"]["schema"]
        generated_schema_422 = generated["paths"][path][method]["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert diff(canonical(contract, contract_schema_422), api_error) == []
        assert diff(canonical(generated, generated_schema_422), api_error) == []


def test_http_validation_error_is_absent_from_the_generated_document(app):
    generated = app.openapi()
    assert "HTTPValidationError" not in generated["components"]["schemas"]
    assert "HTTPValidationError" not in json.dumps(generated)
    assert "ValidationError" not in generated["components"]["schemas"]


def test_health_route_has_no_422(app):
    # The 422 is declared on the API router, not on the app, so /api/health does not gain one.
    operation = app.openapi()["paths"]["/api/health"]["get"]
    assert "422" not in operation["responses"]


# --- the health route (#74) --------------------------------------------------------------------

HEALTH_FASTAPI = {
    "type": "object",
    "title": "HealthResponse",
    "required": ["status"],
    "properties": {"status": {"const": "ok", "type": "string", "title": "Status"}},
}


def health_200_schema(operation):
    return operation["responses"]["200"]["content"]["application/json"]["schema"]


def test_health_route_equals_the_contract(app, contract):
    generated = app.openapi()
    contract_operation = join_server_base(contract)["/api/health"]["get"]
    generated_operation = generated["paths"]["/api/health"]["get"]

    contract_body = health_200_schema(contract_operation)
    generated_body = health_200_schema(generated_operation)
    assert compare_schemas(contract, contract_body, generated, generated_body) == []

    assert operation_security(contract, contract_operation) == []
    assert operation_security(generated, generated_operation) == []

    assert set(contract_operation["responses"]) == {"200"}
    assert set(generated_operation["responses"]) == {"200"}

    # Stated directly as well, so the test does not depend on the normaliser alone.
    resolved = canonical(contract, contract_body)
    assert resolved["required"] == ["status"]
    assert list(resolved["properties"]) == ["status"]


def _contract_health_variant(contract, change):
    variant = copy.deepcopy(contract)
    change(variant["components"]["schemas"]["HealthResponse"])
    schema = health_200_schema(variant["paths"]["/health"]["get"])
    return variant, schema


def _fastapi_health_doc():
    return {"components": {"schemas": {"HealthResponse": copy.deepcopy(HEALTH_FASTAPI)}}}


def test_contract_health_schema_matches_a_fastapi_style_fragment(contract):
    document = _fastapi_health_doc()
    variant, schema = _contract_health_variant(contract, lambda health: None)
    generated = document["components"]["schemas"]["HealthResponse"]
    assert compare_schemas(variant, schema, document, generated) == []


def test_health_status_of_another_type_is_reported(contract):
    document = _fastapi_health_doc()
    variant, schema = _contract_health_variant(
        contract, lambda health: health["properties"]["status"].update(type="integer")
    )
    assert compare_schemas(variant, schema, document, document["components"]["schemas"]["HealthResponse"])


def test_health_extra_property_is_reported(contract):
    document = _fastapi_health_doc()
    variant, schema = _contract_health_variant(
        contract, lambda health: health["properties"].update(version={"type": "string"})
    )
    assert compare_schemas(variant, schema, document, document["components"]["schemas"]["HealthResponse"])


def test_health_status_not_required_is_reported(contract):
    document = _fastapi_health_doc()
    variant, schema = _contract_health_variant(
        contract, lambda health: health.update(required=[])
    )
    assert compare_schemas(variant, schema, document, document["components"]["schemas"]["HealthResponse"])


def test_health_with_bearer_security_differs_from_the_app(contract):
    bearer_operation = {"security": [{"bearerAuth": []}]}
    assert operation_security(contract, bearer_operation) != operation_security(
        fastapi_style_doc(), {}
    )
