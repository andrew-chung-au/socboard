"""Check a saved Terraform plan's actions: `terraform show -json <plan> | python3 scripts/check_plan_actions.py` (#209).

Accepts one known-good shape: every `resource_changes[].change.actions` list is exactly ["no-op"], ["create"],
["update"] or ["read"]. Anything else (a destroy, a replace in either order, a forget, an unknown action, a missing
or unparsable field) fails, naming each refused resource address. It prints only counts and addresses, never an
attribute, to standard output and, when GitHub sets GITHUB_STEP_SUMMARY, to the job summary.

Exit codes: 0 every change is allowed; 1 a resource is refused; 2 the document cannot be read.
VERIFY: the JSON plan format (resource_changes, address, change.actions) against Terraform 1.17.2.
"""

import json
import os
import sys

ALLOWED = {("no-op",), ("create",), ("update",), ("read",)}
REFUSED_KINDS = {
    ("delete",): "destroyed",
    ("delete", "create"): "replaced",
    ("create", "delete"): "replaced",
    ("forget",): "forgotten",
}
UNREADABLE = "plan check: the plan JSON could not be read"


def check(doc):
    """Return (output lines, exit code) for a parsed plan document."""
    changes = doc.get("resource_changes") if isinstance(doc, dict) else None
    # shortcut: a plan with no resources at all omits resource_changes and is refused; the server part always has some.
    if not isinstance(changes, list):
        return [UNREADABLE], 2
    counts = {"add": 0, "change": 0, "replace": 0, "destroy": 0}
    refused = []
    for item in changes:
        address = item.get("address") if isinstance(item, dict) else None
        if not isinstance(address, str) or not address:
            return [UNREADABLE], 2
        change = item.get("change")
        actions = change.get("actions") if isinstance(change, dict) else None
        key = tuple(actions) if isinstance(actions, list) and all(isinstance(a, str) for a in actions) else None
        if key == ("create",):
            counts["add"] += 1
        elif key == ("update",):
            counts["change"] += 1
        elif REFUSED_KINDS.get(key) == "replaced":
            counts["replace"] += 1
        elif key == ("delete",):
            counts["destroy"] += 1
        if key not in ALLOWED:
            kind = REFUSED_KINDS.get(key, "changed by an action list this check does not allow")
            refused.append(f"refused: {json.dumps(address)} would be {kind}")  # quoted: no newline reaches the log
    if any(counts.values()) or refused:
        lines = [f"Plan: {counts['add']} to add, {counts['change']} to change, {counts['replace']} to replace, {counts['destroy']} to destroy."]
    else:
        lines = ["No changes."]
    return lines + refused, 1 if refused else 0


def main():
    try:
        doc = json.loads(sys.stdin.read())
    except (ValueError, UnicodeDecodeError):
        lines, code = [UNREADABLE], 2
    else:
        lines, code = check(doc)
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
