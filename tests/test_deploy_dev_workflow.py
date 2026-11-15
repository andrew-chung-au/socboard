"""The dev deploy workflow and the publish job's hand-over step (#238), checked from their text.

Both files are protected, so until the human applies them they are proposals in `_human-tasks/238-proposed/`:
`deploy-dev.yml` (a new file) and `publish-workflow.diff` (lines appended to `publish.yml`). These tests read
the real file when it exists and the proposal otherwise, and refuse the state where both or neither exist.
Each rule has a test on a small broken copy and one that the real text passes. Nothing here runs Actions.
"""

import copy
import re
from pathlib import Path

import pytest
import workflow_triggers
import yaml

import test_publish_workflow as publish_rules

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PROPOSED = ROOT / "_human-tasks" / "238-proposed"
PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",  # v7.0.1
    "actions/download-artifact": "9000827ccba6bdab643e8b6fd33ac0654aef8333",  # v8.0.2
    "actions/upload-artifact": "cf430e030ddbb5b0abf93d22962f4752f3646cd9",  # v7.0.2
    "aws-actions/configure-aws-credentials": "e1253824e5c10ff9df46874f81ed3ec929e19cfd",  # v6.3.0
}
FIND_CONDITIONS = (
    "github.event.workflow_run.conclusion == 'success'",
    "github.event.workflow_run.event == 'workflow_run'",
    "github.event.workflow_run.head_branch == 'main'",
    "github.event.workflow_run.head_repository.full_name == github.repository",
)
ARTIFACT_KEYS = ("schema", "image", "tag", "digest", "revision")
CHECKOUT = {"persist-credentials": False}
DOWNLOAD = {
    "name": "published-image",
    "path": "${{ runner.temp }}/published",
    "run-id": "${{ github.event.workflow_run.id }}",
    "github-token": "${{ github.token }}",
    "repository": "${{ github.repository }}",
}
FIND_ENV = {"GH_TOKEN": "${{ github.token }}", "HEAD_SHA": "${{ github.event.workflow_run.head_sha }}", "FOUND": "${{ steps.look.outputs.found }}"}
DEPLOY_ENV = {
    "DEPLOY_INSTANCE_ID": "${{ vars.DEPLOY_INSTANCE_ID }}",
    "DEPLOY_URL": "${{ vars.DEPLOY_URL }}",
    "IMAGE": "${{ needs.find.outputs.image }}",
    "TAG": "${{ needs.find.outputs.tag }}",
    "DIGEST": "${{ needs.find.outputs.digest }}",
    "REVISION": "${{ needs.find.outputs.revision }}",
    "RUN_URL": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}",
}


def triggers(doc):
    return doc.get("on", doc.get(True)) or {}  # PyYAML reads the key `on` as True


def steps(job):
    return job.get("steps", [])


def uses(step):
    return str(step.get("uses", ""))


def action(step):
    return uses(step).split("@")[0]


def deploy_dev_text():
    real, proposed = WORKFLOWS / "deploy-dev.yml", PROPOSED / "deploy-dev.yml"
    assert real.exists() != proposed.exists(), "exactly one of .github/workflows/deploy-dev.yml and its proposal must exist"
    return (real if real.exists() else proposed).read_text(encoding="utf-8")


def appended_lines(publish_text, diff_text):
    """The lines a diff appends to the end of publish.yml, or AssertionError if it does anything else."""
    lines = diff_text.splitlines()
    assert lines[:2] == ["--- a/.github/workflows/publish.yml", "+++ b/.github/workflows/publish.yml"], "not a diff of publish.yml"
    hunks = [line for line in lines[2:] if line.startswith("@@")]
    assert len(hunks) == 1, "the diff must be one hunk"
    count = len(publish_text.splitlines())
    match = re.fullmatch(r"@@ -(\d+),0 \+(\d+)(?:,(\d+))? @@.*", hunks[0])
    assert match and int(match.group(1)) == count and int(match.group(2)) == count + 1, "the diff must only append to today's publish.yml"
    body = lines[lines.index(hunks[0]) + 1 :]
    assert all(line.startswith("+") for line in body), "the diff removes or changes a line"
    return [line[1:] for line in body]


def publish_text():
    real = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    diff = PROPOSED / "publish-workflow.diff"
    if not diff.exists():
        return real
    return real.rstrip("\n") + "\n" + "\n".join(appended_lines(real, diff.read_text(encoding="utf-8"))) + "\n"


def load_deploy():
    text = deploy_dev_text()
    return yaml.safe_load(text), text


def load_publish():
    text = publish_text()
    return yaml.safe_load(text), text


# --- deploy-dev.yml --------------------------------------------------------------------------------


def run_blocks(doc):
    return [step.get("run", "") for job in doc["jobs"].values() for step in steps(job)]


def problems(doc, text):
    found = []
    if doc.get("name") != "socboard deploy dev":
        found.append("the workflow is not named socboard deploy dev")
    if doc.get("permissions") != {"contents": "read"}:
        found.append("the workflow's default permissions are not contents: read")
    if (doc.get("defaults") or {}).get("run", {}).get("shell") != "bash":
        found.append("scripts do not run in bash with pipefail")
    on = triggers(doc)
    if set(on) != {"workflow_run"}:
        found.append(f"triggers are {sorted(on)}, not workflow_run only")
    else:
        run_trigger = on["workflow_run"] or {}
        if run_trigger.get("workflows") != ["socboard publish"] or run_trigger.get("types") != ["completed"]:
            found.append("the workflow_run trigger is not 'socboard publish' completed")
        if {"branches", "branches-ignore"} & set(run_trigger):
            found.append("the trigger filters branches (the find job's if decides, visibly)")
    jobs = doc.get("jobs", {})
    if set(jobs) != {"find", "deploy"}:
        found.append(f"jobs are {sorted(jobs)}, not find and deploy")
        return found
    find, deploy = jobs["find"], jobs["deploy"]
    for condition in FIND_CONDITIONS:
        if condition not in str(find.get("if", "")):
            found.append(f"the find job does not require {condition}")
    if "environment" in find:
        found.append("the find job names an Environment")
    if find.get("permissions") != {"actions": "read", "contents": "read"}:
        found.append("the find job's permissions are not actions: read and contents: read")
    if deploy.get("needs") != "find" or str(deploy.get("if", "")).strip() != "needs.find.outputs.deploy == 'true'":
        found.append("the deploy job does not run only when find produced a digest")
    if deploy.get("environment") != "deploy":
        found.append("the deploy job is not in the deploy Environment")
    if deploy.get("permissions") != {"id-token": "write", "contents": "read", "actions": "read"}:
        found.append("the deploy job's permissions are not id-token: write, contents: read, actions: read")
    if deploy.get("concurrency") != {"group": "deploy-dev", "cancel-in-progress": False}:
        found.append("the deploy job's concurrency is not the non-cancelling group deploy-dev")
    if deploy.get("timeout-minutes") != 25:
        found.append("the deploy job's timeout is not 25 minutes")
    writers = {name for name, job in jobs.items() if "write" in (job.get("permissions") or {}).values()}
    if writers != {"deploy"} or any(key != "id-token" and value == "write" for key, value in deploy["permissions"].items()):
        found.append("a write permission other than the deploy job's id-token")
    for name, job in jobs.items():
        for step in steps(job):
            if uses(step) and (action(step) not in PINS or uses(step) != f"{action(step)}@{PINS[action(step)]}"):
                found.append(f"{name} uses {uses(step)}, not one of the pinned actions")
    if set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) != {"AWS_DEPLOY_ROLE_ARN"}:
        found.append("the workflow reads a secret other than AWS_DEPLOY_ROLE_ARN, or none")
    if set(re.findall(r"vars\.([A-Za-z0-9_]+)", text)) != {"DEPLOY_INSTANCE_ID", "DEPLOY_URL"}:
        found.append("the workflow reads variables other than DEPLOY_INSTANCE_ID and DEPLOY_URL")
    for block in run_blocks(doc):
        if "${{" in block:
            found.append("a run block interpolates an expression (values must come through env:)")
            break
    for name, job in jobs.items():
        if "continue-on-error" in job or any("continue-on-error" in step for step in steps(job)):
            found.append(f"{name} has continue-on-error (a failed step must fail the run)")
        for step in steps(job):
            if action(step) == "actions/checkout" and (step.get("with") or {}) != CHECKOUT:
                found.append(f"{name} checks out something other than the default branch's commit without credentials")
    for block in run_blocks(doc):
        if re.search(r"\baws\s", block):
            found.append("a run block calls aws directly (only scripts/ci_deploy.py may)")
            break
    found += deploy_step_problems(deploy)
    found += find_step_problems(find)
    return found


def find_step_problems(find):
    found = []
    downloads = [s for s in steps(find) if action(s) == "actions/download-artifact"]
    if len(downloads) != 1 or (downloads[0].get("with") or {}).get("name") != "published-image":
        found.append("the find job does not download exactly the artifact published-image")
    elif (downloads[0].get("with") or {}) != DOWNLOAD:
        found.append("the find job does not download the artifact of the triggering publish run, with the run's token, from this repository")
    script = [s for s in steps(find) if "scripts/ci_deploy.py find" in s.get("run", "")]
    if len(script) != 1 or "--output \"$GITHUB_OUTPUT\"" not in script[0]["run"]:
        found.append("the find job does not decide with scripts/ci_deploy.py find writing to GITHUB_OUTPUT")
    elif 'main_head="$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)"' not in script[0]["run"] or '--main-head "$main_head"' not in script[0]["run"] or '--head-sha "$HEAD_SHA"' not in script[0]["run"]:
        found.append("the find job does not compare the publish run's commit with the head of main")
    elif (script[0].get("env") or {}) != FIND_ENV:
        found.append("the find step's environment is not the token, the publish run's commit and the look result")
    return found


def deploy_step_problems(deploy):
    found = []
    names = [action(s) or s.get("run", "") for s in steps(deploy)]
    try:
        check = next(i for i, s in enumerate(steps(deploy)) if "scripts/ci_deploy.py check" in s.get("run", ""))
        role = names.index("aws-actions/configure-aws-credentials")
        send = next(i for i, s in enumerate(steps(deploy)) if "scripts/ci_deploy.py deploy" in s.get("run", ""))
        upload = names.index("actions/upload-artifact")
    except (StopIteration, ValueError):
        return ["the deploy job lacks the check, role, deploy or upload step"]
    if not check < role < send < upload == len(names) - 1:
        found.append("the deploy job's steps are not check, role, deploy, then the record upload last")
    credentials = steps(deploy)[role].get("with") or {}
    if credentials.get("role-to-assume") != "${{ secrets.AWS_DEPLOY_ROLE_ARN }}" or credentials.get("aws-region") != "us-east-1":
        found.append("the role is not AWS_DEPLOY_ROLE_ARN in us-east-1")
    if "github.run_id" not in str(credentials.get("role-session-name", "")):
        found.append("the role session name does not include the run id")
    if {"aws-access-key-id", "aws-secret-access-key", "aws-session-token"} & set(credentials):
        found.append("the role step is given a stored key")
    record = steps(deploy)[upload].get("with") or {}
    if record != {"name": "deploy-record-dev", "path": "${{ runner.temp }}/deploy-record-dev.json", "retention-days": 90, "overwrite": True, "if-no-files-found": "error"}:
        found.append("the record is not uploaded as deploy-record-dev, kept 90 days, overwritten on a re-run, failing when missing")
    check_env = steps(deploy)[check].get("env") or {}
    if check_env != {"DEPLOY_INSTANCE_ID": "${{ vars.DEPLOY_INSTANCE_ID }}", "DEPLOY_URL": "${{ vars.DEPLOY_URL }}"}:
        found.append("the check step does not read exactly the two Environment variables")
    if (steps(deploy)[send].get("env") or {}) != DEPLOY_ENV:
        found.append("the deploy step's environment is not the two variables, the find outputs and the run link")
    if "if" in steps(deploy)[upload]:
        found.append("the record upload has its own if (it must run only after every step passed)")
    return found


def test_the_real_workflow_passes_every_rule():
    doc, text = load_deploy()
    assert problems(doc, text) == []


def test_the_workflow_cannot_run_on_a_push_so_the_publish_lists_are_unchanged():
    doc, _ = load_deploy()
    assert workflow_triggers.classify(doc) == (False, False)


def broken(change):
    doc, text = load_deploy()
    doc = copy.deepcopy(doc)
    text = change(doc, text) or text
    return problems(doc, text)


def on(doc):
    return doc[True] if True in doc else doc["on"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda doc, text: on(doc).update(workflow_dispatch=None), "not workflow_run only"),
        (lambda doc, text: on(doc).update(push={"branches": ["main"]}), "not workflow_run only"),
        (lambda doc, text: on(doc)["workflow_run"].update(workflows=["socboard verify"]), "'socboard publish' completed"),
        (lambda doc, text: on(doc)["workflow_run"].update(types=["requested"]), "'socboard publish' completed"),
        (lambda doc, text: on(doc)["workflow_run"].update(branches=["main"]), "filters branches"),
        (lambda doc, text: doc.update(permissions={"contents": "read", "id-token": "write"}), "default permissions"),
        (lambda doc, text: doc.pop("defaults") and None, "pipefail"),
        (lambda doc, text: doc.update(name="deploy"), "not named"),
        (lambda doc, text: doc["jobs"].update(extra={"runs-on": "ubuntu-latest"}), "not find and deploy"),
    ],
    ids=["manual trigger", "push trigger", "other workflow", "requested", "branch filter", "wide default", "no bash", "name", "third job"],
)
def test_a_wrong_trigger_or_workflow_setting_is_refused(change, expected):
    found = broken(change)
    assert any(expected in line for line in found), found


@pytest.mark.parametrize("condition", FIND_CONDITIONS)
def test_a_find_job_that_forgets_a_condition_is_refused(condition):
    def change(doc, text):
        doc["jobs"]["find"]["if"] = doc["jobs"]["find"]["if"].replace(condition, "true")

    assert any(condition in line for line in broken(change))


@pytest.mark.parametrize(
    ("job", "key", "value", "expected"),
    [
        ("find", "environment", "deploy", "find job names an Environment"),
        ("find", "permissions", {"actions": "read", "contents": "read", "id-token": "write"}, "find job's permissions"),
        ("deploy", "environment", "production", "not in the deploy Environment"),
        ("deploy", "environment", None, "not in the deploy Environment"),
        ("deploy", "permissions", {"id-token": "write", "contents": "write", "actions": "read"}, "deploy job's permissions"),
        ("deploy", "permissions", {"id-token": "write", "contents": "read", "actions": "read", "packages": "write"}, "deploy job's permissions"),
        ("deploy", "if", "always()", "only when find produced a digest"),
        ("deploy", "needs", None, "only when find produced a digest"),
        ("deploy", "concurrency", {"group": "deploy-dev", "cancel-in-progress": True}, "non-cancelling group"),
        ("deploy", "concurrency", None, "non-cancelling group"),
        ("deploy", "timeout-minutes", 360, "timeout"),
    ],
)
def test_a_wrong_job_setting_is_refused(job, key, value, expected):
    def change(doc, text):
        if value is None:
            doc["jobs"][job].pop(key, None)
        else:
            doc["jobs"][job][key] = value

    found = broken(change)
    assert any(expected in line for line in found), found


@pytest.mark.parametrize(
    "ref",
    [
        "actions/checkout@v7",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b0",
        "aws-actions/configure-aws-credentials@v6.3.0",
        "someone/deploy-action@1111111111111111111111111111111111111111",
    ],
)
def test_an_unpinned_or_unknown_action_is_refused(ref):
    def change(doc, text):
        doc["jobs"]["deploy"]["steps"][0]["uses"] = ref

    assert any("not one of the pinned actions" in line for line in broken(change))


def test_a_second_secret_is_refused():
    assert any("secret other than" in line for line in broken(lambda doc, text: text + "\n# ${{ secrets.GITHUB_PAT }}\n"))


def test_a_third_variable_is_refused():
    assert any("variables other than" in line for line in broken(lambda doc, text: text + "\n# ${{ vars.DEPLOY_PASSWORD }}\n"))


def run_step(doc, job, needle):
    return next(s for s in doc["jobs"][job]["steps"] if needle in s.get("run", ""))


@pytest.mark.parametrize(
    "expression",
    ["${{ github.event.workflow_run.head_sha }}", "${{ needs.find.outputs.digest }}", "${{ vars.DEPLOY_URL }}"],
)
def test_an_expression_inside_a_run_block_is_refused(expression):
    def change(doc, text):
        step = run_step(doc, "deploy", "scripts/ci_deploy.py deploy")
        step["run"] = step["run"].replace('"$DIGEST"', f'"{expression}"')

    assert any("interpolates an expression" in line for line in broken(change))


def test_a_direct_aws_call_is_refused():
    def change(doc, text):
        doc["jobs"]["deploy"]["steps"].insert(3, {"run": "aws ssm send-command --document-name AWS-RunShellScript"})

    assert any("calls aws directly" in line for line in broken(change))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda deploy: deploy["steps"].reverse(), "not check, role, deploy"),
        (lambda deploy: deploy["steps"].append({"run": "echo done"}), "not check, role, deploy"),
        (lambda deploy: deploy["steps"].pop(1), "lacks the check"),
        (lambda deploy: deploy["steps"][2]["with"].update({"role-to-assume": "${{ vars.ROLE }}"}), "AWS_DEPLOY_ROLE_ARN in us-east-1"),
        (lambda deploy: deploy["steps"][2]["with"].update({"aws-region": "eu-west-1"}), "AWS_DEPLOY_ROLE_ARN in us-east-1"),
        (lambda deploy: deploy["steps"][2]["with"].update({"role-session-name": "socboard"}), "session name"),
        (lambda deploy: deploy["steps"][2]["with"].update({"aws-access-key-id": "x"}), "stored key"),
        (lambda deploy: deploy["steps"][-1]["with"].update({"retention-days": 7}), "kept 90 days"),
        (lambda deploy: deploy["steps"][-1]["with"].update({"name": "deploy-record"}), "deploy-record-dev"),
        (lambda deploy: deploy["steps"][-1].update({"if": "always()"}), "its own if"),
        (lambda deploy: deploy["steps"][-1]["with"].pop("overwrite"), "overwritten on a re-run"),
        (lambda deploy: deploy["steps"][0]["with"].update(ref="${{ needs.find.outputs.revision }}"), "default branch's commit"),
        (lambda deploy: deploy["steps"][0]["with"].update({"persist-credentials": True}), "default branch's commit"),
        (lambda deploy: deploy["steps"][3].update({"continue-on-error": True}), "continue-on-error"),
        (lambda deploy: deploy.update({"continue-on-error": True}), "continue-on-error"),
        (lambda deploy: deploy["steps"][3]["env"].update(DIGEST="${{ needs.find.outputs.tag }}"), "deploy step's environment"),
        (lambda deploy: deploy["steps"][3]["env"].update(DEPLOY_URL="${{ vars.DEPLOY_INSTANCE_ID }}"), "deploy step's environment"),
        (lambda deploy: deploy["steps"][1]["env"].update(DEPLOY_URL="${{ vars.OTHER }}"), "check step"),
    ],
    ids=["reversed", "step after the upload", "no check", "role from a variable", "region", "session name", "stored key", "retention", "record name", "upload always", "no overwrite", "checkout ref", "credentials kept", "step continues", "job continues", "digest from the tag", "swapped variables", "check reads another"],
)
def test_a_wrong_deploy_step_is_refused(change, expected):
    def apply(doc, text):
        change(doc["jobs"]["deploy"])

    found = broken(apply)
    assert any(expected in line for line in found), found


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda find: find["steps"][2]["with"].update({"name": "tested-image"}), "exactly the artifact published-image"),
        (lambda find: find["steps"][2]["with"].update({"run-id": "${{ github.run_id }}"}), "triggering publish run"),
        (lambda find: find["steps"][3].update(run=find["steps"][3]["run"].replace('--output "$GITHUB_OUTPUT"', "")), "writing to GITHUB_OUTPUT"),
        (lambda find: find["steps"][3].update(run=find["steps"][3]["run"].replace("commits/main", "commits/HEAD")), "head of main"),
        (lambda find: find["steps"][3].update(run=find["steps"][3]["run"].replace('--main-head "$main_head"', '--main-head "$HEAD_SHA"')), "head of main"),
        (lambda find: find["steps"][2]["with"].pop("github-token"), "run's token"),
        (lambda find: find["steps"][2]["with"].update(repository="someone/socboard"), "from this repository"),
        (lambda find: find["steps"][3]["env"].update(HEAD_SHA="${{ github.sha }}"), "find step's environment"),
        (lambda find: find["steps"][0]["with"].update(ref="${{ github.event.workflow_run.head_sha }}"), "default branch's commit"),
        (lambda find: find["steps"][3].update({"continue-on-error": True}), "continue-on-error"),
    ],
    ids=["other artifact", "own run", "no output file", "not main", "main head is the publish commit", "no token", "other repository", "other commit", "checkout ref", "continue on error"],
)
def test_a_wrong_find_step_is_refused(change, expected):
    def apply(doc, text):
        change(doc["jobs"]["find"])

    found = broken(apply)
    assert any(expected in line for line in found), found


def test_exactly_one_of_the_workflow_and_its_proposal_exists():
    assert (WORKFLOWS / "deploy-dev.yml").exists() != (PROPOSED / "deploy-dev.yml").exists()


# --- the publish job's hand-over step (publish.yml, protected) ----------------------------------------


def handover_problems(doc):
    found = []
    publish = doc["jobs"]["publish"]
    if publish.get("permissions") != {"actions": "read", "packages": "write"}:
        found.append("the publish job's permissions changed")
    job_steps = steps(publish)
    if len(job_steps) < 2:
        return ["the publish job has no hand-over steps"]
    upload, write = job_steps[-1], job_steps[-2]
    settings = upload.get("with") or {}
    if action(upload) != "actions/upload-artifact" or uses(upload) != f"actions/upload-artifact@{PINS['actions/upload-artifact']}":
        found.append("the publish job's last step is not the pinned upload of published-image")
    if settings.get("name") != "published-image" or settings.get("path") != "published-image.json":
        found.append("the artifact is not published-image with published-image.json")
    if settings.get("retention-days") != 7 or settings.get("overwrite") is not True or settings.get("if-no-files-found") != "error":
        found.append("the artifact is not kept 7 days, overwritten on a re-run and required")
    if "if" in upload or "if" in write:
        found.append("a hand-over step has its own if (it must run only after the pull-back comparison)")
    command = write.get("run", "")
    if "published-image.json" not in command or any(f"{key}: ${key}" not in command for key in ARTIFACT_KEYS[1:]) or "schema: 1" not in command:
        found.append("the step before the upload does not write the five keys to published-image.json")
    if (write.get("env") or {}).get("DIGEST") != "${{ steps.push.outputs.digest }}" or '--arg revision "$SHA"' not in command:
        found.append("the digest or the revision does not come from the push step and the run's commit")
    previous = job_steps[-3] if len(job_steps) >= 3 else {}
    if "Pull it back by digest" not in str(previous.get("name", "")):
        found.append("the hand-over does not follow the pull-back comparison")
    return found


def test_the_publish_workflow_with_the_hand_over_passes_its_own_rules_and_these():
    doc, text = load_publish()
    assert publish_rules.problems(doc, text) == []
    assert handover_problems(doc) == []


def broken_publish(change):
    doc, _ = load_publish()
    doc = copy.deepcopy(doc)
    change(doc["jobs"]["publish"])
    return handover_problems(doc)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda job: job["steps"][-1]["with"].update({"retention-days": 90}), "kept 7 days"),
        (lambda job: job["steps"][-1]["with"].pop("overwrite"), "overwritten on a re-run"),
        (lambda job: job["steps"][-1]["with"].update({"name": "image"}), "published-image with"),
        (lambda job: job["steps"][-1].update(uses="actions/upload-artifact@v7"), "pinned upload"),
        (lambda job: job["steps"][-1].update({"if": "always()"}), "its own if"),
        (lambda job: job["steps"][-2].update(run=job["steps"][-2]["run"].replace("revision: $revision", "")), "five keys"),
        (lambda job: job["steps"][-2]["env"].update(DIGEST="${{ needs.gate.outputs.tag }}"), "push step"),
        (lambda job: job["steps"].insert(-3, job["steps"].pop(-3)) or job["steps"].insert(-2, job["steps"].pop()), "the pinned upload"),
        (lambda job: job["permissions"].update({"id-token": "write"}), "permissions changed"),
    ],
    ids=["retention", "no overwrite", "name", "unpinned", "always", "key missing", "digest source", "order", "permission"],
)
def test_a_wrong_hand_over_is_refused(change, expected):
    found = broken_publish(change)
    assert any(expected in line for line in found), found


def test_the_proposed_diff_only_appends_to_todays_publish_workflow():
    diff = PROPOSED / "publish-workflow.diff"
    if not diff.exists():
        assert handover_problems(yaml.safe_load((WORKFLOWS / "publish.yml").read_text(encoding="utf-8"))) == []
        return
    real = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    added = appended_lines(real, diff.read_text(encoding="utf-8"))
    assert added and not any("secrets." in line for line in added)


@pytest.mark.parametrize(
    "diff",
    [
        "--- a/.github/workflows/compose.yml\n+++ b/.github/workflows/compose.yml\n@@ -1,0 +2,1 @@\n+x\n",
        "--- a/.github/workflows/publish.yml\n+++ b/.github/workflows/publish.yml\n@@ -3,0 +4,1 @@\n+x\n",
        "--- a/.github/workflows/publish.yml\n+++ b/.github/workflows/publish.yml\n@@ -{n},1 +{n},1 @@\n-a\n+b\n",
    ],
    ids=["other file", "insert in the middle", "changes a line"],
)
def test_a_diff_that_does_more_than_append_is_refused(diff):
    real = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        appended_lines(real, diff.replace("{n}", str(len(real.splitlines()))))
