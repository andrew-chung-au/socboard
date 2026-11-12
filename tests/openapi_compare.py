"""Structural comparison of the hand-written contract with FastAPI's generated schema (#10).

The contract (`openapi.yaml`) and the generated document describe the same API in different
forms. `canonical()` reduces a schema to a form where equivalent schemas are equal, and `diff()`
lists where two canonical forms differ. Each normalisation is listed here and again in the
module docstring of test_backend_openapi.py, with what differs, which side changes and why it is
safe. Later endpoint issues (#14 onward) extend the comparison with this module.

Normalisations (applied to BOTH sides, so neither side is privileged):

1. `$ref` is resolved and inlined (local refs only). The contract shares `Id` by reference while
   pydantic inlines `string`. Safe: the resolved structure is what a client sees. A `$ref` with
   sibling keys (3.1 allows them) merges the siblings over the target.
2. `title`, `description`, `examples`, `example`, `minLength`, `default` and `summary` are
   dropped. FastAPI adds `title` everywhere and `default` for optional fields, the contract adds
   prose and `minLength` on `Id`. Safe: none changes which JSON documents are accepted that
   the clients rely on in Core (the contract keeps `minLength` for opaque ids; the backend
   validates ids in handlers).
3. `allOf` is flattened: `properties` are merged, `required` is unioned, other keys must agree
   (else ValueError). The contract writes `CaseDetail` as `allOf`, FastAPI emits one flat object.
4. A nullable alternative set (`oneOf` or `anyOf` with exactly one non-null member plus
   `{type: "null"}`) becomes `{"nullable": <member>}`. The contract writes `oneOf`, pydantic
   writes `anyOf`; with one non-null member the two mean the same. Any other `oneOf`/`anyOf`
   keeps its keyword, so `oneOf` against `anyOf` for non-null alternatives is reported.
5. `required` and `enum` are compared as sorted lists (order carries no meaning).
6. Security schemes are compared by `type`, `scheme`, `in` and `name`, never by key name (the
   contract says `bearerAuth` and `ingestKey`, FastAPI names a scheme after its dependency class).
"""

import json

IGNORED_KEYS = frozenset(
    {"title", "description", "examples", "example", "minLength", "default", "summary"}
)
_SCHEME_FIELDS = ("type", "scheme", "in", "name")


def resolve_pointer(document, ref):
    if not ref.startswith("#/"):
        raise ValueError(f"only local references are supported: {ref}")
    node = document
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _sort_key(value):
    return json.dumps(value, sort_keys=True)


def canonical(document, schema, _seen=()):
    """Reduce `schema` (from `document`) to a comparable plain structure."""
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in _seen:
            raise ValueError(f"circular reference: {ref}")
        target = canonical(document, resolve_pointer(document, ref), _seen + (ref,))
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        merged = dict(target)
        if siblings:
            merged.update(canonical(document, siblings, _seen))
        return merged

    work = {key: value for key, value in schema.items() if key not in IGNORED_KEYS}

    if "allOf" in work:
        parts = [canonical(document, part, _seen) for part in work.pop("allOf")]
        parts.append(canonical(document, work, _seen))
        return _merge_all_of(parts)

    for keyword in ("oneOf", "anyOf"):
        if keyword in work:
            alternatives = [canonical(document, alt, _seen) for alt in work.pop(keyword)]
            rest = canonical(document, work, _seen)
            null = {"type": "null"}
            non_null = [alt for alt in alternatives if alt != null]
            if len(non_null) == 1 and len(alternatives) == 2:
                result = {**rest, "nullable": non_null[0]}
            else:
                result = {**rest, keyword: sorted(alternatives, key=_sort_key)}
            return result

    result = {}
    for key, value in work.items():
        if key == "properties":
            result[key] = {name: canonical(document, sub, _seen) for name, sub in value.items()}
        elif key == "items":
            result[key] = canonical(document, value, _seen)
        elif key == "required":
            result[key] = sorted(value)
        elif key == "enum":
            result[key] = sorted(value, key=_sort_key)
        elif key == "type" and isinstance(value, list):
            result[key] = sorted(value)
        elif key == "additionalProperties" and isinstance(value, dict):
            result[key] = canonical(document, value, _seen)
        else:
            result[key] = value
    return result


def _merge_all_of(parts):
    merged = {}
    for part in parts:
        for key, value in part.items():
            if key == "properties":
                merged.setdefault("properties", {}).update(value)
            elif key == "required":
                merged["required"] = sorted(set(merged.get("required", [])) | set(value))
            elif key in merged and merged[key] != value:
                raise ValueError(f"allOf members disagree on {key!r}")
            else:
                merged[key] = value
    return merged


def diff(left, right, path="$"):
    """List the differences between two canonical structures, as readable strings."""
    if isinstance(left, dict) and isinstance(right, dict):
        found = []
        for key in sorted(set(left) | set(right)):
            if key not in left:
                found.append(f"{path}.{key}: only on the right")
            elif key not in right:
                found.append(f"{path}.{key}: only on the left")
            else:
                found.extend(diff(left[key], right[key], f"{path}.{key}"))
        return found
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [f"{path}: lists differ in length ({len(left)} against {len(right)})"]
        found = []
        for index, (a, b) in enumerate(zip(left, right)):
            found.extend(diff(a, b, f"{path}[{index}]"))
        return found
    if left != right:
        return [f"{path}: {left!r} against {right!r}"]
    return []


def compare_schemas(left_doc, left_schema, right_doc, right_schema):
    return diff(canonical(left_doc, left_schema), canonical(right_doc, right_schema))


# --- security ----------------------------------------------------------------------------------


def scheme_signature(scheme):
    return tuple(scheme.get(field) for field in _SCHEME_FIELDS)


def operation_security(document, operation):
    """The operation's security as a sorted list of requirements, each a sorted tuple of
    scheme signatures. An operation inherits the document's `security` unless it sets its own."""
    schemes = document.get("components", {}).get("securitySchemes", {})
    requirements = operation.get("security", document.get("security", []))
    result = []
    for requirement in requirements:
        signatures = sorted(
            (scheme_signature(schemes[name]) for name in requirement),
            key=lambda sig: tuple("" if part is None else part for part in sig),
        )
        result.append(tuple(signatures))
    return sorted(result, key=repr)


# --- paths -------------------------------------------------------------------------------------


def join_server_base(document):
    """Paths of the contract with `servers[0].url` put in front (the contract's paths are
    relative to the server base `/api`; FastAPI's carry the router prefix)."""
    base = document["servers"][0]["url"].rstrip("/")
    return {base + path: item for path, item in document["paths"].items()}
