"""Reads of observability/CONTRACT.md that more than one test needs (#339)."""

import re
from pathlib import Path

CONTRACT_MD = Path(__file__).resolve().parent.parent / "observability" / "CONTRACT.md"


def canaries(text: str | None = None) -> dict[str, str]:
    """The canary strings of CONTRACT.md section 8, as {kind: value}; the block there is the only list."""
    text = CONTRACT_MD.read_text(encoding="utf-8") if text is None else text
    block = text.split("## 8.", 1)[1].split("```text", 1)[1].split("```", 1)[0]
    return dict(re.findall(r"^([a-z-]+)=(\S+)$", block, re.MULTILINE))
