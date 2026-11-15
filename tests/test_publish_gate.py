"""scripts/publish_gate.py (#235): when the image may be pushed, what it is tagged and named.

Every value is made up. The runs are shaped like GitHub's `workflow_runs` objects.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publish_gate", ROOT / "scripts" / "publish_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
REPO = "andrew-chung-au/socboard"
NAMES = ("socboard verify", "socboard postgres", "socboard image", "socboard compose")


def run(name, id, status="completed", conclusion="success", sha=SHA, event="push", branch="main", repository=REPO):
    return {
        "id": id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "event": event,
        "head_branch": branch,
        "head_repository": {"full_name": repository},
    }


def all_green():
    return [run(name, 100 + number) for number, name in enumerate(NAMES)]


def decide(runs, **overrides):
    facts = {"repository": REPO, "sha": SHA, "event": "push", "branch": "main", "head_repository": REPO, "runs": runs}
    facts.update(overrides)
    return gate.decide(**facts)


def test_every_required_check_green_is_ready_and_names_the_compose_run():
    ready, reason, compose = decide(all_green())
    assert (ready, compose) == (True, 103)
    assert "succeeded" in reason


def test_a_terraform_check_that_ran_must_have_succeeded_too():
    runs = all_green() + [run("terraform check", 200, conclusion="failure")]
    ready, reason, _ = decide(runs)
    assert not ready and "terraform check (failure)" in reason


def test_a_terraform_check_that_did_not_run_is_not_waited_for():
    assert decide(all_green())[0] is True  # a commit that touched nothing under terraform/ has no such run


def test_a_workflow_added_later_is_waited_for_without_a_change_to_the_gate():
    runs = all_green() + [run("a new check", 300, status="in_progress", conclusion=None)]
    ready, reason, _ = decide(runs)
    assert not ready and reason.startswith("not yet") and "a new check" in reason


@pytest.mark.parametrize("name", NAMES)
def test_a_missing_required_run_is_not_yet(name):
    ready, reason, compose = decide([r for r in all_green() if r["name"] != name])
    assert (ready, compose) == (False, None)
    assert reason.startswith("not yet") and name in reason


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending"])
def test_a_run_that_has_not_completed_is_not_yet(status):
    runs = all_green()
    runs[1] = run(NAMES[1], 101, status=status, conclusion=None)
    ready, reason, _ = decide(runs)
    assert not ready and reason.startswith("not yet") and NAMES[1] in reason


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out", "neutral", None])
def test_a_run_that_did_not_succeed_is_refused(conclusion):
    runs = all_green()
    runs[3] = run(NAMES[3], 103, conclusion=conclusion)
    ready, reason, compose = decide(runs)
    assert (ready, compose) == (False, None)
    assert reason.startswith("refused") and NAMES[3] in reason


def test_of_two_runs_of_one_workflow_the_newer_decides():
    older_failed = run(NAMES[2], 50, conclusion="failure")
    runs = all_green() + [older_failed]  # a re-run that succeeded has the larger id
    assert decide(runs)[0] is True
    newer_failed = run(NAMES[2], 500, conclusion="failure")
    assert decide(all_green() + [newer_failed])[0] is False


def test_the_publish_workflows_own_runs_and_other_commits_runs_are_ignored():
    runs = all_green() + [
        run("socboard publish", 900, status="in_progress", conclusion=None),
        run("terraform check", 901, conclusion="failure", sha=OTHER),
        run(NAMES[0], 902, conclusion="failure", sha=OTHER),
    ]
    assert decide(runs)[0] is True


def test_the_same_commit_pushed_to_another_branch_neither_hides_a_failure_on_main_nor_blocks_it():
    failed_on_main = [run(NAMES[0], 100, conclusion="failure")] + [r for r in all_green() if r["name"] != NAMES[0]]
    green_elsewhere = run(NAMES[0], 900, branch="feature")  # a newer, green run of the same sha on another branch
    assert decide(failed_on_main + [green_elsewhere])[0] is False
    running_elsewhere = run("socboard verify", 901, status="in_progress", conclusion=None, branch="feature")
    assert decide(all_green() + [running_elsewhere])[0] is True


def test_runs_of_the_same_commit_from_a_fork_do_not_count():
    fork_failure = run(NAMES[1], 950, conclusion="failure", repository="someone-else/socboard")
    assert decide(all_green() + [fork_failure])[0] is True
    only_fork = [r for r in all_green() if r["name"] != NAMES[3]] + [run(NAMES[3], 951, repository="someone-else/socboard")]
    ready, reason, _ = decide(only_fork)
    assert not ready and NAMES[3] in reason


def test_a_run_whose_repository_is_missing_does_not_count():
    nameless = run(NAMES[2], 960)
    nameless["head_repository"] = None
    assert decide([r for r in all_green() if r["name"] != NAMES[2]] + [nameless])[0] is False


def test_a_pull_request_run_of_the_same_commit_does_not_count_for_a_push():
    runs = [r for r in all_green() if r["name"] != NAMES[3]] + [run(NAMES[3], 777, event="pull_request")]
    ready, reason, _ = decide(runs)
    assert not ready and NAMES[3] in reason


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "pull_request"},
        {"event": "workflow_dispatch"},
        {"branch": "feature"},
        {"branch": "main2"},
        {"head_repository": "someone-else/socboard"},
    ],
)
def test_only_a_push_to_main_of_this_repository_can_be_ready(overrides):
    ready, reason, compose = decide(all_green(), **overrides)
    assert (ready, compose) == (False, None) and "not a push to main of this repository" in reason


# --- the tag and the name ------------------------------------------------------------------------


def test_the_tag_is_the_commit_time_in_utc_and_seven_characters_of_the_sha():
    assert gate.tag_for(SHA, "2026-11-15T09:12:34+10:00") == "20261114-231234-0123456"
    assert gate.tag_for(SHA, "2026-11-15T09:12:34Z") == "20261115-091234-0123456"
    assert gate.tag_for(SHA, "2026-11-15T09:12:34.567-05:00") == "20261115-141234-0123456"


def test_the_same_commit_always_gets_the_same_tag_and_two_commits_in_one_second_differ():
    assert gate.tag_for(SHA, "2026-11-15T09:12:34Z") == gate.tag_for(SHA, "2026-11-15T09:12:34Z")
    assert gate.tag_for(SHA, "2026-11-15T09:12:34Z") != gate.tag_for(OTHER, "2026-11-15T09:12:34Z")


def test_the_tag_is_never_latest_and_always_sorts_by_time():
    tags = [gate.tag_for(SHA, f"2026-11-1{day}T00:00:00Z") for day in range(1, 6)]
    assert "latest" not in "".join(tags) and tags == sorted(tags)


@pytest.mark.parametrize("sha", ["", "latest", SHA.upper(), SHA[:39], SHA + "0", "g" * 40])
def test_a_sha_that_is_not_forty_lower_case_hex_characters_is_refused(sha):
    with pytest.raises(gate.BadInput):
        gate.tag_for(sha, "2026-11-15T09:12:34Z")


@pytest.mark.parametrize(
    "stamp",
    [
        "",
        "yesterday",
        "2026-11-15",
        "2026-11-15T09:12:34",
        "2026-11-15 09:12:34Z",
        "2026-11-15T09:12:34Z; rm -rf /",
        "2026-13-45T09:12:34Z",
        "9999-12-31T23:59:59-01:00",
        "٢٠٢٦-١١-١٥T٠٩:١٢:٣٤Z",
    ],
)
def test_a_timestamp_that_is_not_iso_with_an_offset_is_refused(stamp):
    with pytest.raises(gate.BadInput):
        gate.tag_for(SHA, stamp)


def test_the_image_name_is_the_repository_in_lower_case_on_ghcr():
    assert gate.image_name("Andrew-Chung-AU/SOCBoard") == "ghcr.io/andrew-chung-au/socboard"


@pytest.mark.parametrize("repository", ["", "socboard", "a/b/c", "a b/c", "a/b;c", "../x", "a/..", "a/.", "-a/b", "a_b/c"])
def test_a_repository_that_is_not_owner_slash_name_is_refused(repository):
    with pytest.raises(gate.BadInput):
        gate.image_name(repository)


# --- the command line ----------------------------------------------------------------------------


def test_the_gate_command_prints_the_output_lines(tmp_path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps(all_green()), encoding="utf-8")
    argv = ["gate", "--repository", REPO, "--sha", SHA, "--event", "push", "--branch", "main", "--head-repository", REPO, "--runs", str(runs)]
    assert gate.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == ["ready=true", "reason=every check of the commit succeeded", "compose_run_id=103"]


def test_a_refusal_is_still_exit_zero_with_ready_false(tmp_path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps([r for r in all_green() if r["name"] != NAMES[0]]), encoding="utf-8")
    argv = ["gate", "--repository", REPO, "--sha", SHA, "--event", "push", "--branch", "main", "--head-repository", REPO, "--runs", str(runs)]
    assert gate.main(argv) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "ready=false" and out[2] == "compose_run_id="


@pytest.mark.parametrize("content", ["", "not json", "{}", "[1, 2]", f'[{{"head_sha": "{SHA}", "event": "push", "head_branch": "main", "head_repository": {{"full_name": "{REPO}"}}, "name": "x"}}]'])
def test_unusable_runs_are_exit_two_without_a_traceback(tmp_path, capsys, content):
    runs = tmp_path / "runs.json"
    runs.write_text(content, encoding="utf-8")
    argv = ["gate", "--repository", REPO, "--sha", SHA, "--event", "push", "--branch", "main", "--head-repository", REPO, "--runs", str(runs)]
    assert gate.main(argv) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_tag_and_name_commands_and_a_bad_argument(capsys):
    assert gate.main(["tag", "--sha", SHA, "--timestamp", "2026-11-15T09:12:34Z"]) == 0
    assert gate.main(["name", "--repository", "A/B"]) == 0
    assert capsys.readouterr().out.splitlines() == ["20261115-091234-0123456", "ghcr.io/a/b"]
    assert gate.main(["tag", "--sha", "nope", "--timestamp", "2026-11-15T09:12:34Z"]) == 2
    assert gate.main(["frobnicate"]) == 2
