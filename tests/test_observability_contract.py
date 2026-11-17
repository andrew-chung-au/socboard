"""observability/contract.yaml against observability/CONTRACT.md (#339).

The Markdown is the document people change first; the YAML is its machine copy. The test builds the
same structure from the Markdown tables and fails naming the first difference. PyYAML is already a
locked dev dependency (the workflow tests use it), so the file is YAML, not JSON.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml
from observability_support import CONTRACT_MD, canaries

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_YAML = ROOT / "observability" / "contract.yaml"
RULE_FILE = ROOT / "observability" / "alerts" / "queue_latency.yml"

MD = CONTRACT_MD.read_text(encoding="utf-8")
MEANING = re.compile(
    r"(\d+)th percentile of `(\w+)`, over the last (\d+) minutes, is above (\d+) seconds, "
    r"and `([^`]+)` is at least (\d+)"
)


# ---- reading CONTRACT.md -------------------------------------------------------------------------


def sections() -> dict[int, str]:
    parts = re.split(r"^## (\d+)\. .*$", MD, flags=re.MULTILINE)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2])}


def tables(body: str) -> dict[tuple, list[dict]]:
    """Every pipe table of a section, keyed by its header cells. Pipes inside backticks are not splits."""
    found, lines = {}, body.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].startswith("|"):
            block.append([c.strip() for c in re.split(r"\|(?=(?:[^`]*`[^`]*`)*[^`]*$)", lines[i].strip())[1:-1]])
            i += 1
        header, rows = block[0], [r for r in block[2:]]
        assert all(len(r) == len(header) for r in rows), f"a table row of {header} has the wrong number of cells"
        found[tuple(header)] = [dict(zip(header, r)) for r in rows]
    return found


def table(section: int, *header: str) -> list[dict]:
    got = tables(sections()[section])
    assert header in got, f"CONTRACT.md section {section} has no table with the columns {header}; it has {list(got)}"
    return got[header]


def plain(cell: str) -> str:
    return cell.replace("`", "")


def ticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def value_cell(cell: str) -> dict:
    """Allowed values when the cell opens with backticked words, otherwise the cell's words."""
    head = cell.split(" (")[0]
    return {"values": ticks(head)} if head.startswith("`") else {"description": plain(cell)}


def bucket_bounds() -> dict[str, list[float]]:
    section = sections()[2]
    first = re.search(r"`(\S+)` has explicit bucket bounds, in seconds: `([^`]+)`", section)
    same = re.search(r"`(\S+)` has the same bounds", section)
    assert first and same, "section 2 no longer states the bucket bounds and 'the same bounds'"
    bounds = [float(b) for b in first.group(2).split(",")]
    return {first.group(1): bounds, same.group(1): bounds}


def prometheus_names(cell: str) -> list[str]:
    base, *suffixes = ticks(cell)
    stem = base.removesuffix("_bucket")
    return [base] + [stem + s for s in suffixes]


def alert_expression(six: str) -> str:
    meaning = MEANING.search(six)
    assert meaning, "section 6 no longer says the percentile, window, threshold and volume"
    percentile, metric, window, threshold, volume, guard = meaning.groups()
    return (
        f"histogram_quantile({int(percentile) / 100}, sum by (le) (rate({metric}[{window}m]))) > {threshold} "
        f"and {volume} >= {guard}"
    )


def contract_from_markdown() -> dict:
    bounds = bucket_bounds()
    metrics = []
    for r in table(2, "Name", "Type", "Unit", "Labels (allowed values)", "Prometheus name after the collector", "Implemented by"):
        name = ticks(r["Name"])[0]
        cell = r["Labels (allowed values)"]
        labels = {} if cell.startswith("none") else {k: ticks(v) for k, v in re.findall(r"`(\w+)` \(([^)]*)\)", cell)}
        metric = {"name": name, "type": r["Type"], "unit": ticks(r["Unit"])[0], "labels": labels}
        if r["Type"] == "histogram":
            assert name in bounds, f"no bucket bounds stated for {name}"
            metric["bucket_bounds"] = bounds[name]
        metric["prometheus_names"] = prometheus_names(r["Prometheus name after the collector"])
        metrics.append(metric)

    spans = [
        {
            "name": ticks(r["Span name"])[0] if "`" in r["Span name"] else r["Span name"],
            "kind": r["Kind"],
            "parent": plain(r["Parent"]),
            "link": plain(r["Link"]),
            "attributes": [] if r["Allowed attributes"].startswith("none") else ticks(r["Allowed attributes"]),
        }
        for r in table(3, "Span name", "Kind", "Parent", "Link", "Allowed attributes", "Implemented by")
    ]
    span_attributes = [
        {"name": ticks(r["Attribute"])[0], "type": r["Type"], **value_cell(r["Allowed values"])}
        for r in table(3, "Attribute", "Type", "Allowed values")
    ]

    resource = [
        {
            "name": ticks(r["Name"])[0],
            "component": "probe" if "(the probe)" in r["Name"] else "app",
            "type": r["Type"],
            **value_cell(r["Value"]),
            "set_from": plain(r["Set from"]),
        }
        for r in table(1, "Name", "Type", "Value", "Set from", "Implemented by")
    ]
    log_fields = [
        {"name": ticks(r["Field"])[0], "type": r["Type"], **value_cell(r["Value"])}
        for r in table(4, "Field", "Type", "Value")
    ]
    log_events = [
        {
            "name": ticks(r["Event"])[0],
            "when": r["When"],
            "extra_fields": [] if r["Extra fields"].startswith("none") else ticks(r["Extra fields"]),
        }
        for r in table(4, "Event", "When", "Extra fields", "Implemented by")
    ]

    six = sections()[6]
    parts = {r["Part"]: r["Value"] for r in table(6, "Part", "Value")}
    annotations = [
        {"name": ticks(r["Annotation"])[0], "content": plain(r["Content"]), "source": plain(r["Source"])}
        for r in table(6, "Annotation", "Content", "Source")
    ]
    assert ticks(parts["Annotations"]) == [a["name"] for a in annotations], "section 6 lists different annotations twice"
    alert = {
        "name": plain(parts["Name"]),
        "for": plain(parts["`for`"]),
        "labels": {ticks(k)[0]: plain(v) for k, v in parts.items() if k.startswith("Label ")},
        "expression": alert_expression(six),
        "annotations": annotations,
    }
    payload = [{"field": plain(r["Field"]), "meaning": plain(r["Meaning"])} for r in table(7, "Field", "Meaning")]
    return {
        "resource_attributes": resource,
        "metrics": metrics,
        "spans": spans,
        "span_attributes": span_attributes,
        "log_fields": log_fields,
        "log_events": log_events,
        "alert": alert,
        "alertmanager_payload": payload,
    }


# ---- comparing -----------------------------------------------------------------------------------


def first_difference(expected, actual, path="contract") -> str | None:
    """The first place `actual` (the YAML) differs from `expected` (the Markdown), named by its path."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in [*expected, *(k for k in actual if k not in expected)]:
            if key not in actual:
                return f"{path}.{key}: missing from the YAML (CONTRACT.md has {expected[key]!r})"
            if key not in expected:
                return f"{path}.{key}: not in CONTRACT.md (the YAML has {actual[key]!r})"
            found = first_difference(expected[key], actual[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(expected, list) and isinstance(actual, list):
        for i in range(max(len(expected), len(actual))):
            if i >= len(actual):
                return f"{path}[{i}]: missing from the YAML (CONTRACT.md has {expected[i]!r})"
            if i >= len(expected):
                return f"{path}[{i}]: not in CONTRACT.md (the YAML has {actual[i]!r})"
            label = expected[i].get("name", i) if isinstance(expected[i], dict) else i
            found = first_difference(expected[i], actual[i], f"{path}[{label}]")
            if found:
                return found
        return None
    if expected != actual:
        return f"{path}: CONTRACT.md has {expected!r}, the YAML has {actual!r}"
    return None


def load_yaml() -> dict:
    return yaml.safe_load(CONTRACT_YAML.read_text(encoding="utf-8"))


def comparable(data: dict) -> dict:
    """The YAML without the rule-file values of the annotations, which CONTRACT.md does not hold."""
    data = copy.deepcopy(data)
    for annotation in data["alert"]["annotations"]:
        annotation.pop("value", None)
    return data


def difference(data: dict) -> str | None:
    return first_difference(contract_from_markdown(), comparable(data))


# ---- tests ---------------------------------------------------------------------------------------


def test_the_yaml_matches_the_contract():
    assert difference(load_yaml()) is None


def test_the_how_to_use_note_is_at_the_top():
    head = CONTRACT_YAML.read_text(encoding="utf-8").split("\n\n")[0]
    assert head.startswith("# How to use this file")
    assert "CONTRACT.md is the document people read and change first" in head.replace("\n# ", " ").replace("#", "")
    assert "same pull" in head.replace("\n# ", " ") and "Tests read this file" in head.replace("\n# ", " ")


def test_the_yaml_holds_no_canary_string():
    text = CONTRACT_YAML.read_text(encoding="utf-8")
    found = canaries()
    assert len(found) == 7
    assert not [value for value in found.values() if value in text] and "CANARY" not in text


def test_the_alert_file_matches_the_yaml():
    alert = load_yaml()["alert"]
    (rule,) = [r for g in yaml.safe_load(RULE_FILE.read_text(encoding="utf-8"))["groups"] for r in g["rules"]]
    assert rule["alert"] == alert["name"]
    assert rule["for"] == alert["for"]
    assert rule["labels"] == alert["labels"]
    assert rule["expr"] == alert["expression"]
    assert rule["annotations"] == {a["name"]: a["value"] for a in alert["annotations"]}


# Each mutation edits an in-memory copy of the YAML, never a file; each kind of field must be refused.
MUTATIONS = {
    "one bucket bound": lambda d: d["metrics"][1]["bucket_bounds"].__setitem__(3, 3.5),
    "an extra span attribute": lambda d: d["spans"][1]["attributes"].append("http.url"),
    "a metric name": lambda d: d["metrics"][0].__setitem__("name", "socboard.ingest.alert"),
    "a metric type": lambda d: d["metrics"][0].__setitem__("type", "gauge"),
    "a metric unit": lambda d: d["metrics"][1].__setitem__("unit", "ms"),
    "a metric label": lambda d: d["metrics"][0]["labels"].__setitem__("reason", ["x"]),
    "a label value": lambda d: d["metrics"][0]["labels"]["outcome"].append("error"),
    "a prometheus name": lambda d: d["metrics"][2]["prometheus_names"].__setitem__(0, "socboard_probe_seconds_bucket"),
    "a span name": lambda d: d["spans"][2].__setitem__("name", "queue.seen"),
    "a span kind": lambda d: d["spans"][0].__setitem__("kind", "internal"),
    "a span parent": lambda d: d["spans"][1].__setitem__("parent", "none"),
    "a span link": lambda d: d["spans"][2].__setitem__("link", "none"),
    "an attribute value": lambda d: d["span_attributes"][0]["values"].append("Urgent"),
    "a resource attribute": lambda d: d["resource_attributes"].pop(),
    "a log field": lambda d: d["log_fields"].append({"name": "case_id", "type": "string"}),
    "a log field type": lambda d: d["log_fields"][0].__setitem__("type", "number"),
    "an event name": lambda d: d["log_events"][1].__setitem__("name", "alert.seen"),
    "an event extra field": lambda d: d["log_events"][0]["extra_fields"].append("title"),
    "the alert name": lambda d: d["alert"].__setitem__("name", "QueueSlow"),
    "the alert for": lambda d: d["alert"].__setitem__("for", "5m"),
    "an alert label": lambda d: d["alert"]["labels"].__setitem__("severity", "ticket"),
    "an annotation name": lambda d: d["alert"]["annotations"][3].__setitem__("name", "pager"),
    "an annotation source": lambda d: d["alert"]["annotations"][0].__setitem__("source", "the series"),
    "the alert expression": lambda d: d["alert"].__setitem__("expression", d["alert"]["expression"].replace("> 5", "> 6")),
    "a payload field": lambda d: d["alertmanager_payload"].append({"field": "alerts[].generatorURL", "meaning": "x"}),
}


@pytest.mark.parametrize("what", MUTATIONS)
def test_a_changed_copy_is_refused_naming_the_difference(what):
    data = load_yaml()
    MUTATIONS[what](data)
    found = difference(data)
    assert found and found.startswith("contract."), what


def test_the_refusal_names_the_first_difference():
    data = load_yaml()
    data["metrics"][1]["bucket_bounds"][3] = 3.5
    data["spans"][1]["attributes"].append("http.url")
    assert difference(data) == (
        "contract.metrics[socboard.alert.time_to_queue].bucket_bounds[3]: CONTRACT.md has 3.0, the YAML has 3.5"
    )


def test_an_extra_span_attribute_is_named():
    data = load_yaml()
    data["spans"][1]["attributes"].append("http.url")
    assert difference(data) == (
        "contract.spans[ingest.alert].attributes[2]: not in CONTRACT.md (the YAML has 'http.url')"
    )
