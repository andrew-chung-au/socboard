"""scripts/check_plan_actions.py (#209): plan-JSON fixtures written here; no AWS, no Terraform."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_plan_actions.py"
UNREADABLE = "plan check: the plan JSON could not be read\n"


def run(stdin, tmp_path=None):
    env = {"GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")} if tmp_path else {}
    return subprocess.run([sys.executable, "-I", str(SCRIPT)], input=stdin, capture_output=True, text=True, env=env)


def plan(*changes):
    return json.dumps(
        {
            "format_version": "1.2",
            "resource_changes": [
                {"address": address, "type": "aws_instance", "change": {"actions": actions, "before": {"secret_like": "s3cr3t"}, "after": {"ip": "203.0.113.9"}}}
                for address, actions in changes
            ],
        }
    )


def test_allowed_actions_pass_and_print_counts_and_no_attribute(tmp_path):
    result = run(
        plan(
            ("aws_instance.server", ["no-op"]),
            ("aws_eip.server", ["create"]),
            ("aws_security_group.server", ["create"]),
            ("aws_ebs_volume.data", ["update"]),
            ("data.aws_ami.ubuntu", ["read"]),
        ),
        tmp_path,
    )
    expected = "Plan: 2 to add, 1 to change, 0 to replace, 0 to destroy.\n"
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
    assert (tmp_path / "summary.md").read_text() == expected
    assert "s3cr3t" not in result.stdout and "203.0.113.9" not in result.stdout


def test_only_no_op_says_no_changes():
    result = run(plan(("aws_instance.server", ["no-op"]), ("data.aws_ami.ubuntu", ["read"])))
    assert (result.returncode, result.stdout) == (0, "No changes.\n")


OTHER = "would be changed by an action list this check does not allow"


@pytest.mark.parametrize(
    ("actions", "words", "counts"),
    [
        (["delete", "create"], "would be replaced", "1 to add, 0 to change, 1 to replace, 0 to destroy."),
        (["create", "delete"], "would be replaced", "1 to add, 0 to change, 1 to replace, 0 to destroy."),
        (["delete"], "would be destroyed", "1 to add, 0 to change, 0 to replace, 1 to destroy."),
        (["forget"], "would be forgotten", "1 to add, 0 to change, 0 to replace, 0 to destroy."),
        (["obliterate"], OTHER, "1 to add, 0 to change, 0 to replace, 0 to destroy."),
        ([], OTHER, "1 to add, 0 to change, 0 to replace, 0 to destroy."),
        (["create", "update"], OTHER, "1 to add, 0 to change, 0 to replace, 0 to destroy."),
        ("create", OTHER, "1 to add, 0 to change, 0 to replace, 0 to destroy."),
        ([None], OTHER, "1 to add, 0 to change, 0 to replace, 0 to destroy."),
    ],
)
def test_refused_actions_fail_and_name_the_address(actions, words, counts):
    result = run(plan(("aws_eip.server", ["create"]), ("aws_ebs_volume.data", actions)))
    assert result.returncode == 1
    assert result.stdout.splitlines() == [f"Plan: {counts}", f'refused: "aws_ebs_volume.data" {words}']


def test_a_refused_forget_alone_does_not_say_no_changes():
    result = run(plan(("aws_ebs_volume.data", ["forget"])))
    assert result.returncode == 1
    assert result.stdout.splitlines()[0] == "Plan: 0 to add, 0 to change, 0 to replace, 0 to destroy."


@pytest.mark.parametrize(
    "change",
    [{"address": "aws_ebs_volume.data"}, {"address": "aws_ebs_volume.data", "change": None}, {"address": "aws_ebs_volume.data", "change": {}}],
)
def test_a_missing_change_or_actions_field_fails_naming_the_address(change):
    result = run(json.dumps({"resource_changes": [change]}))
    assert result.returncode == 1
    assert '"aws_ebs_volume.data"' in result.stdout


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "not json",
        "[]",
        "null",
        json.dumps({"format_version": "1.2"}),
        json.dumps({"resource_changes": {"address": "x"}}),
        json.dumps({"resource_changes": ["aws_instance.server"]}),
        json.dumps({"resource_changes": [{"change": {"actions": ["create"]}}]}),
        json.dumps({"resource_changes": [{"address": 7, "change": {"actions": ["create"]}}]}),
    ],
)
def test_a_malformed_document_fails_with_the_fixed_message(stdin):
    result = run(stdin)
    assert (result.returncode, result.stdout, result.stderr) == (2, UNREADABLE, "")


def test_an_address_with_a_newline_cannot_start_a_log_line():
    result = run(plan(("aws_eip.x[\"a\n::error::b\"]", ["delete"])))
    assert result.returncode == 1
    assert not any(line.startswith("::") for line in result.stdout.splitlines())
