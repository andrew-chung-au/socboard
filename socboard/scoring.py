"""Rule-based severity scoring (#12).

`score(alert_type, criticality)` is a pure function over the explicit table below. Matching of the
alert type is exact and case-sensitive, with no trimming. The product rule (the table and the
Medium defaults) is approved with issue #12; changing a value needs the human's approval.

This module imports only `socboard.domain` (plus the standard library names the test allows), so
it has no clock, no random source, no database and no machine learning by construction.
"""

from dataclasses import dataclass
from typing import Final

from socboard.domain import ASSET_CRITICALITIES, SEVERITIES

KNOWN_TYPES: Final = ("failed_login", "malware_detection", "unusual_network_traffic")

# One row per (alert type, criticality) pair.
SEVERITY_TABLE: Final[dict[tuple[str, str], str]] = {
    ("failed_login", "Low"): "Low",
    ("failed_login", "Medium"): "Low",
    ("failed_login", "High"): "Medium",
    ("failed_login", "Critical"): "High",
    ("malware_detection", "Low"): "Medium",
    ("malware_detection", "Medium"): "High",
    ("malware_detection", "High"): "Critical",
    ("malware_detection", "Critical"): "Critical",
    ("unusual_network_traffic", "Low"): "Low",
    ("unusual_network_traffic", "Medium"): "Medium",
    ("unusual_network_traffic", "High"): "High",
    ("unusual_network_traffic", "Critical"): "High",
}

DEFAULT_SEVERITY: Final = "Medium"  # an unknown alert type
DEFAULT_CRITICALITY: Final = "Medium"  # an asset that is not in the inventory

UNKNOWN_TYPE_RULE: Final = "unknown type, default -> Medium"


@dataclass(frozen=True)
class Score:
    severity: str
    rule: str


def score(alert_type: str, criticality: str | None) -> Score:
    """The severity and the rule that produced it.

    `criticality` is one of the four values, or None when the asset is not in the inventory. The
    rule string never contains the submitted type or asset name.
    """
    if alert_type not in KNOWN_TYPES:
        return Score(DEFAULT_SEVERITY, UNKNOWN_TYPE_RULE)
    if criticality is None:
        used = DEFAULT_CRITICALITY
        severity = SEVERITY_TABLE[(alert_type, used)]
        return Score(severity, f"{alert_type} x {used} (asset unknown, criticality {used}) -> {severity}")
    if criticality not in ASSET_CRITICALITIES:
        raise ValueError("criticality must be one of the four values or None")
    severity = SEVERITY_TABLE[(alert_type, criticality)]
    assert severity in SEVERITIES
    return Score(severity, f"{alert_type} x {criticality} -> {severity}")
