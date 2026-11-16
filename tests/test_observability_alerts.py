"""The QueueLatencyOverSLO rule (#296), checked against observability/CONTRACT.md.

Two parts. The static check reads the rule and section 6 of the contract and returns one refusal per
difference; each mutated copy of the rule must be refused and the real file must pass. The promtool
check runs `promtool test rules` and `promtool check rules` in the pinned Prometheus image through
Docker; it is marked `docker_only` and is deselected, with the count printed, when no Docker answers.
"""

import copy
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ALERTS = ROOT / "observability" / "alerts"
RULE = ALERTS / "queue_latency.yml"
PROMTOOL_TESTS = ALERTS / "tests" / "queue_latency_test.yml"
CONTRACT = ROOT / "observability" / "CONTRACT.md"

# VERIFY: the digest is the index digest of the v3.16.0 tag, read from the Docker Hub registry API on 2026-11-16
IMAGE = "prom/prometheus:v3.16.0@sha256:e04987641b57704f408e31f6373ba4138ecf27cb4a165d24aabb6c38fddd0f06"

MEANING = re.compile(
    r"(\d+)th percentile of `(\w+)`, over the last (\d+) minutes, is above (\d+) seconds, "
    r"and `([^`]+)` is at least (\d+)"
)


def section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def row(text: str, name: str) -> str:
    """The value cell of the table row whose first cell is `name`, without its backticks."""
    found = re.search(rf"^\| {re.escape(name)} \| (.+?) \|$", text, re.MULTILINE)
    assert found, f"no row {name!r} in section 6"
    return found.group(1).replace("`", "")


def bounds(contract: str) -> list[str]:
    found = re.search(r"explicit bucket bounds, in seconds: `([^`]+)`", contract)
    assert found, "no bucket bound line in the contract"
    return [bound.strip() for bound in found.group(1).split(",")]


def problems(rule_file: dict, contract: str) -> list[str]:
    """One refusal per difference between the rule file and section 6 of the contract."""
    six = section(contract, "## 6. Alert", "## 7.")
    meaning = MEANING.search(six)
    assert meaning, "section 6 no longer says the percentile, window, threshold and volume"
    percentile, metric, window, threshold, volume, guard = meaning.groups()
    expected_expr = (
        f"histogram_quantile({int(percentile) / 100}, sum by (le) (rate({metric}[{window}m]))) > {threshold} "
        f"and {volume} >= {guard}"
    )
    found = []
    groups = rule_file.get("groups") or []
    rules = [rule for group in groups for rule in group.get("rules", [])]
    if len(groups) != 1 or len(rules) != 1:
        return ["the file must hold one group with one rule"]
    rule = rules[0]
    if rule.get("alert") != row(six, "Name"):
        found.append(f"name: {rule.get('alert')!r}")
    if " ".join(str(rule.get("expr", "")).split()) != expected_expr:
        found.append(f"expr: {rule.get('expr')!r} is not {expected_expr!r}")
    if rule.get("for") != row(six, "`for`"):
        found.append(f"for: {rule.get('for')!r}")
    labels = {"severity": row(six, "Label `severity`"), "service": row(six, "Label `service`")}
    if rule.get("labels") != labels:
        found.append(f"labels: {rule.get('labels')!r} is not {labels!r}")
    names = [name.strip() for name in row(six, "Annotations").split(",")]
    annotations = rule.get("annotations") or {}
    if sorted(annotations) != sorted(names):
        found.append(f"annotations: {sorted(annotations)!r} is not {sorted(names)!r}")
    for name, label in (("environment", "deployment_environment_name"), ("version", "service_version")):
        if label not in str(annotations.get(name, "")):
            found.append(f"annotation {name} does not read {label}")
    for name in ("summary", "owner", "dashboard_url"):
        if not str(annotations.get(name, "")).strip():
            found.append(f"annotation {name} is empty")
    return found


def real_rule() -> dict:
    return yaml.safe_load(RULE.read_text(encoding="utf-8"))


def only_rule(rule_file: dict) -> dict:
    return rule_file["groups"][0]["rules"][0]


def with_expr(old: str, new: str):
    def mutate(rule):
        expr = only_rule(rule)["expr"]
        assert old in expr
        only_rule(rule)["expr"] = expr.replace(old, new)

    return mutate


def without(key: str):
    return lambda rule: only_rule(rule).pop(key)


def without_annotation(rule):
    del only_rule(rule)["annotations"]["dashboard_url"]


def severity_ticket(rule):
    only_rule(rule)["labels"]["severity"] = "ticket"


MUTATIONS = {
    "threshold 10 s": with_expr("> 5 and", "> 10 and"),
    "no for": without("for"),
    "the in-app histogram as the source": with_expr("socboard_probe_", "socboard_alert_"),
    "a missing annotation": without_annotation,
    "a 4 minute window": with_expr("[5m]", "[4m]"),
    "a guard of 10": with_expr(">= 20", ">= 10"),
    "another severity": severity_ticket,
}


def test_the_rule_matches_the_contract():
    assert problems(real_rule(), CONTRACT.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_a_changed_rule_is_refused(name):
    mutated = copy.deepcopy(real_rule())
    MUTATIONS[name](mutated)
    assert problems(mutated, CONTRACT.read_text(encoding="utf-8")) != []


def test_the_promtool_cases_use_the_contract_bucket_bounds():
    expected = set(bounds(CONTRACT.read_text(encoding="utf-8"))) | {"+Inf"}
    cases = yaml.safe_load(PROMTOOL_TESTS.read_text(encoding="utf-8"))["tests"]
    checked = 0
    for case in cases:
        found = set()
        for item in case["input_series"]:
            match = re.fullmatch(r'socboard_probe_time_to_queue_seconds_bucket\{le="([^"]+)"\}', item["series"])
            if match:
                found.add(match.group(1))
        if found:
            checked += 1
            assert found == expected, case["name"]
    assert checked >= 5


def test_the_image_is_pinned_by_tag_and_index_digest():
    assert re.fullmatch(r"prom/prometheus:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", IMAGE)


@pytest.mark.docker_only("promtool runs from the pinned Prometheus image through Docker")
@pytest.mark.parametrize(
    "args",
    [("test", "rules", "/alerts/tests/queue_latency_test.yml"), ("check", "rules", "/alerts/queue_latency.yml")],
    ids=["test-rules", "check-rules"],
)
def test_promtool_passes(args):
    done = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "/bin/promtool", "--volume", f"{ALERTS}:/alerts:ro", IMAGE, *args],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert done.returncode == 0, done.stdout + done.stderr
