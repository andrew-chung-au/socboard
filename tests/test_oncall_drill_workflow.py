"""The proposed on-call drill workflow (#299), checked from its text. Nothing here runs Actions, Docker or a model.

The file lives at observability/drill/proposed/oncall-drill.yml until the CI lane or the maintainer lands it
as .github/workflows/oncall-drill.yml (a protected path). These tests hold the job contract of
observability/oncall/MANDATE.md to the file: who holds the model credential, what each job may write, where
the guard comes from, and which action commit is pinned.
"""

import re
import subprocess
from pathlib import Path

import pytest
import workflow_triggers
import yaml

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "observability" / "drill"
PROPOSED = DRILL / "proposed" / "oncall-drill.yml"
PATCH = DRILL / "proposed" / "workflow-changes.patch"
MANDATE = (ROOT / "observability" / "oncall" / "MANDATE.md").read_text(encoding="utf-8")
TEXT = PROPOSED.read_text(encoding="utf-8")
DOC = yaml.safe_load(TEXT)
JOBS = DOC["jobs"]
CREDENTIAL = "CLAUDE_CODE_OAUTH_TOKEN"


def steps(job):
    return JOBS[job]["steps"]


def step_text(job):
    return yaml.dump(JOBS[job])


def test_it_starts_by_hand_only_and_runs_on_no_push():
    assert set(workflow_triggers.events(DOC)) == {"workflow_dispatch"}
    assert workflow_triggers.classify(DOC) == (False, False)  # no entry in publish.yml's trigger list


def test_the_five_jobs_and_their_order():
    assert list(JOBS) == ["drill", "agent", "publish", "check", "report"]
    assert JOBS["agent"]["needs"] == "drill"
    assert JOBS["publish"]["needs"] == ["drill", "agent"]
    assert JOBS["check"]["needs"] == "publish"
    assert set(JOBS["report"]["needs"]) == {"drill", "agent", "publish", "check"}
    assert JOBS["report"]["if"] == "always()"


def test_each_jobs_permissions():
    assert DOC["permissions"] == {"contents": "read"}
    assert JOBS["drill"]["permissions"] == {"contents": "read"}
    assert JOBS["agent"]["permissions"] == {"contents": "read"}
    assert JOBS["publish"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert JOBS["check"]["permissions"] == {"contents": "read"}
    assert JOBS["report"]["permissions"] == {"contents": "read", "actions": "read"}


def test_only_the_agent_job_reads_a_secret_and_it_is_the_model_credential():
    assert re.findall(r"secrets\.([A-Za-z_]+)", TEXT) == [CREDENTIAL]
    assert f"secrets.{CREDENTIAL}" in step_text("agent")
    assert JOBS["agent"]["environment"] == "oncall"
    for name in ("drill", "publish", "check", "report"):
        assert "environment" not in JOBS[name]
        assert "secrets." not in step_text(name)


def test_the_mandate_names_the_same_credential():
    assert CREDENTIAL in MANDATE


def test_the_action_is_pinned_to_the_commit_the_mandate_names():
    pinned = re.search(r"Pinned action commit: ([0-9a-f]{40})", MANDATE).group(1)
    used = re.findall(r"uses: anthropics/claude-code-action@(\S+)", TEXT)
    assert used == [pinned]


def test_the_tool_lists_are_the_mandates():
    block = re.search(r"claude_args: \|\n((?:  .+\n)+)", MANDATE).group(1)
    wanted = [line.strip() for line in block.splitlines()]
    action = next(s for s in steps("agent") if "claude-code-action" in str(s.get("uses")))
    assert [line.strip() for line in action["with"]["claude_args"].splitlines() if line.strip()] == wanted


def test_the_action_gets_the_read_only_token_and_the_agent_job_scrubs_the_environment():
    action = next(s for s in steps("agent") if "claude-code-action" in str(s.get("uses")))
    assert action["with"]["github_token"] == "${{ github.token }}"
    assert JOBS["agent"]["env"]["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert "use_commit_signing" not in action["with"] and "mcp_config" not in action["with"]


def test_checkouts_keep_no_credentials_except_the_publish_job():
    for name, job in JOBS.items():
        for item in job["steps"]:
            if str(item.get("uses", "")).startswith("actions/checkout"):
                keep = item["with"]["persist-credentials"]
                assert keep is (name == "publish"), name


def test_the_agent_job_checks_the_git_config_before_and_after_the_agent():
    names = [s.get("name", "") for s in steps("agent")]
    assert names.index("Record the repository's git config") < names.index("The on-call agent") < names.index("Write the patch")
    patch = next(s for s in steps("agent") if s.get("name") == "Write the patch")
    assert patch["env"]["GIT_CONFIG_NOSYSTEM"] == "1" and patch["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert "/usr/bin/git" in patch["run"] and "core.hooksPath=/dev/null" in patch["run"] and "core.fsmonitor=false" in patch["run"]


def test_the_publish_job_runs_the_guard_from_main_before_it_applies_or_pushes_anything():
    text = step_text("publish")
    assert "ref: main" in text
    first = {name: text.index(name) for name in ("cp observability/oncall/guard.py", "check-patch", "git apply", "git push")}
    assert first["cp observability/oncall/guard.py"] < first["check-patch"] < first["git apply"]
    assert first["check-patch"] < first["git push"]
    for push in steps("publish"):
        if "git push" in str(push.get("run")):
            assert push["if"] == "steps.guard.outputs.guard_exit == '0'"
    assert "check-pr" in text and "draft=true" in text and "base=\"drill/$RUN_ID\"" in text
    assert "oncall/$RUN_ID" in text


def test_no_job_merges_approves_or_enables_auto_merge():
    for word in ("gh pr merge", "gh pr review", "--approve", "/reviews", "--auto", "enable_auto_merge", "enablePullRequestAutoMerge", "pull_request_target"):
        assert word not in TEXT, word
    assert "gh pr create" not in TEXT  # REST only: gh api


def test_the_check_job_runs_on_the_drill_branch_without_a_secret():
    assert JOBS["check"]["if"] == "needs.publish.outputs.open_pr == 'true'"
    checkout = next(s for s in steps("check") if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "drill/${{ github.run_id }}"
    assert "observability/drill/prove.py" in step_text("check")


def test_the_report_job_scans_every_other_jobs_log():
    text = step_text("report")
    assert "observability/drill/logscan.py" in text and "observability/drill/summary.py" in text
    assert '.name != "report"' in TEXT  # the report job's own log is the only one left out


def test_the_scripts_the_workflow_calls_exist():
    for name in set(re.findall(r"observability/drill/[A-Za-z_]+\.(?:py|sh|mjs)", TEXT)):
        assert (ROOT / name).is_file(), name
    assert (ROOT / "observability" / "oncall" / "guard.py").is_file()


def test_the_job_scripts_are_valid_shell():
    for name in ("drill.sh", "drift.sh"):
        assert subprocess.run(["bash", "-n", str(DRILL / name)]).returncode == 0, name


def test_the_drill_script_plants_the_defect_runs_the_probe_and_the_handoff_and_stops_the_stack():
    text = (DRILL / "drill.sh").read_text(encoding="utf-8")
    for needle in ("git apply observability/drill/defect.patch", "scripts/observability_probe.py", "observability/oncall/handoff.py", "observability/drill/bundle.py", "compose down --volumes", "trap stop EXIT"):
        assert needle in text, needle
    assert "git commit" not in text and "git push" not in text  # main never holds the defect


def test_the_drift_check_is_called_by_check_sh_and_uses_the_patch():
    assert "bash observability/drill/drift.sh" in (ROOT / "observability" / "ci" / "check.sh").read_text(encoding="utf-8")
    drift = (DRILL / "drift.sh").read_text(encoding="utf-8")
    assert "defect.patch" in drift and "uv run --locked pytest" in drift


def test_the_workflow_diff_applies_to_the_live_workflows_and_does_what_it_says():
    text = PATCH.read_text(encoding="utf-8")
    assert subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=ROOT).returncode == 0
    added = [line[1:].strip() for line in text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    assert added.count("types: [opened, synchronize, reopened, ready_for_review]") == 2
    assert "timeout-minutes: 30" in added
    for name in ("verify.yml", "compose.yml"):
        assert f"a/.github/workflows/{name}" in text


@pytest.mark.parametrize("name", ["verify.yml", "compose.yml"])
def test_ready_for_review_leaves_the_push_triggers_alone(name):
    # Only the pull_request types change, so the answers of tests/workflow_triggers.py stay as they are.
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    assert workflow_triggers.classify(doc) == (True, True)
