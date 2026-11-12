"""Severity scoring (#12): the table, the defaults, the rule strings and the pure-module rule."""

import ast
from pathlib import Path

import pytest

from socboard import domain, scoring
from socboard.scoring import score

TYPES = ("failed_login", "malware_detection", "unusual_network_traffic")

EXPECTED = {
    "failed_login": ("Low", "Low", "Medium", "High"),
    "malware_detection": ("Medium", "High", "Critical", "Critical"),
    "unusual_network_traffic": ("Low", "Medium", "High", "High"),
}
CELLS = [
    (alert_type, level, severity)
    for alert_type, severities in EXPECTED.items()
    for level, severity in zip(domain.ASSET_CRITICALITIES, severities, strict=True)
]


@pytest.mark.parametrize(("alert_type", "level", "severity"), CELLS)
def test_every_cell_of_the_table(alert_type, level, severity):
    result = score(alert_type, level)
    assert result.severity == severity
    assert result.rule == f"{alert_type} x {level} -> {severity}"


def test_the_table_has_exactly_one_row_per_pair():
    pairs = {(t, c) for t in TYPES for c in domain.ASSET_CRITICALITIES}
    assert set(scoring.SEVERITY_TABLE) == pairs
    assert len(scoring.SEVERITY_TABLE) == len(pairs) == 12
    assert set(scoring.SEVERITY_TABLE.values()) <= set(domain.SEVERITIES)
    assert scoring.KNOWN_TYPES == TYPES


def test_the_example_rule_string():
    assert score("malware_detection", "High").rule == "malware_detection x High -> Critical"


@pytest.mark.parametrize("alert_type", TYPES)
def test_unknown_asset_uses_medium(alert_type):
    result = score(alert_type, None)
    expected = score(alert_type, "Medium").severity
    assert result.severity == expected
    assert result.rule == f"{alert_type} x Medium (asset unknown, criticality Medium) -> {expected}"


def test_unknown_asset_example_rule_string():
    result = score("malware_detection", None)
    assert result.rule == "malware_detection x Medium (asset unknown, criticality Medium) -> High"


@pytest.mark.parametrize("level", [*domain.ASSET_CRITICALITIES, None])
@pytest.mark.parametrize(
    "alert_type",
    ["Malware_Detection", "malware_detection ", " malware_detection", "", "other", "FAILED_LOGIN"],
)
def test_unknown_type_is_medium_whatever_the_criticality(alert_type, level):
    result = score(alert_type, level)
    assert result.severity == "Medium"
    assert result.rule == "unknown type, default -> Medium"


@pytest.mark.parametrize("level", [*domain.ASSET_CRITICALITIES, None])
def test_a_rule_string_never_holds_the_submitted_type(level):
    result = score("<script>alert(1)</script>", level)
    assert result.rule == "unknown type, default -> Medium"
    assert "script" not in result.rule


def test_a_bad_criticality_is_refused():
    with pytest.raises(ValueError):
        score("failed_login", "high")


def test_the_same_inputs_give_equal_results():
    assert score("failed_login", "High") == score("failed_login", "High")
    assert score("x", None) == score("x", None)


def test_the_longest_rule_string_fits_in_200_characters():
    rules = [score(t, c).rule for t in TYPES for c in (*domain.ASSET_CRITICALITIES, None)]
    rules.append(score("unknown", None).rule)
    assert max(len(rule) for rule in rules) <= 200


def test_the_module_imports_only_the_allowed_modules():
    source = Path(scoring.__file__).read_text(encoding="utf-8")
    allowed = {"socboard.domain", "dataclasses", "typing", "enum"}
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module)
    assert imported <= allowed, imported - allowed
