"""The promote-to-prod workflow (#269), checked from its text.

The file is protected, so until the human installs it as `.github/workflows/promote-prod.yml` it is a proposal in
`_human-tasks/269-proposed/`. These tests read the real file when it exists and the proposal otherwise, and refuse
the state where both or neither exist. Each rule has a test on a small broken copy and one that the real text
passes. Nothing here runs Actions.
"""

import copy
import re
from pathlib import Path

import pytest
import workflow_triggers
import yaml

import test_deploy_dev_workflow as dev_rules

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / ".github" / "workflows" / "promote-prod.yml"
PROPOSED = ROOT / "_human-tasks" / "269-proposed" / "promote-prod.yml"
PINS = dev_rules.PINS
MAIN_ONLY = "github.ref == 'refs/heads/main'"
RESOLVE_ENV = {"GH_TOKEN": "${{ github.token }}", "REPOSITORY": "${{ github.repository }}", "INPUT_DIGEST": "${{ inputs.digest }}"}
PROMOTE_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "ACTOR": "${{ github.actor }}",
    "REPOSITORY": "${{ github.repository }}",
    "DEPLOY_INSTANCE_ID": "${{ vars.DEPLOY_INSTANCE_ID }}",
    "DEPLOY_URL": "${{ vars.DEPLOY_URL }}",
    "IMAGE": "${{ needs.resolve.outputs.image }}",
    "TAG": "${{ needs.resolve.outputs.tag }}",
    "DIGEST": "${{ needs.resolve.outputs.digest }}",
    "REVISION": "${{ needs.resolve.outputs.revision }}",
    "PREVIOUS": "${{ needs.resolve.outputs.previous }}",
    "RUN_URL": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}",
}
RECORD = {"name": "deploy-record-prod", "path": "${{ runner.temp }}/deploy-record-prod.json", "retention-days": 90, "overwrite": True, "if-no-files-found": "error"}
MASK = {"env": {"AWS_DEPLOY_ROLE_ID": "${{ secrets.AWS_DEPLOY_ROLE_ID }}"}, "run": 'echo "::add-mask::$AWS_DEPLOY_ROLE_ID"'}


def load():
    assert REAL.exists() != PROPOSED.exists(), "exactly one of .github/workflows/promote-prod.yml and its proposal must exist"
    text = (REAL if REAL.exists() else PROPOSED).read_text(encoding="utf-8")
    return yaml.safe_load(text), text


def steps(job):
    return job.get("steps", [])


def action(step):
    return str(step.get("uses", "")).split("@")[0]


def problems(doc, text):
    found = []
    if doc.get("name") != "socboard promote prod":
        found.append("the workflow is not named socboard promote prod")
    if doc.get("permissions") != {"contents": "read"}:
        found.append("the workflow's default permissions are not contents: read")
    if (doc.get("defaults") or {}).get("run", {}).get("shell") != "bash":
        found.append("scripts do not run in bash with pipefail")
    on = dev_rules.triggers(doc)
    if set(on) != {"workflow_dispatch"}:
        found.append(f"triggers are {sorted(on)}, not workflow_dispatch only")
    else:
        inputs = (on["workflow_dispatch"] or {}).get("inputs") or {}
        if set(inputs) != {"digest"}:
            found.append("the inputs are not exactly digest")
        elif inputs["digest"].get("required") is not False or inputs["digest"].get("type") != "string" or inputs["digest"].get("default") != "":
            found.append("the digest input is not an optional string defaulting to empty")
    jobs = doc.get("jobs", {})
    if set(jobs) != {"resolve", "promote"}:
        found.append(f"jobs are {sorted(jobs)}, not resolve and promote")
        return found
    resolve, promote = jobs["resolve"], jobs["promote"]
    for name, job in jobs.items():
        if str(job.get("if", "")).strip() != MAIN_ONLY:
            found.append(f"the {name} job does not run only on main")
    if "environment" in resolve:
        found.append("the resolve job names an Environment (it must refuse before an approval is asked)")
    if resolve.get("permissions") != {"actions": "read", "contents": "read"}:
        found.append("the resolve job's permissions are not actions: read and contents: read")
    if promote.get("needs") != "resolve":
        found.append("the promote job does not need resolve")
    if promote.get("environment") != "deploy-prod":
        found.append("the promote job is not in the deploy-prod Environment")
    if promote.get("permissions") != {"id-token": "write", "contents": "read", "actions": "read"}:
        found.append("the promote job's permissions are not id-token: write, contents: read, actions: read")
    if promote.get("concurrency") != {"group": "deploy-prod", "cancel-in-progress": False}:
        found.append("the promote job's concurrency is not the non-cancelling group deploy-prod")
    writers = {name for name, job in jobs.items() if "write" in (job.get("permissions") or {}).values()}
    if writers != {"promote"}:
        found.append("a job other than promote has a write permission")
    for name, job in jobs.items():
        if "continue-on-error" in job or any("continue-on-error" in step for step in steps(job)):
            found.append(f"{name} has continue-on-error (a failed step must fail the run)")
        for step in steps(job):
            if step.get("uses") and (action(step) not in PINS or step["uses"] != f"{action(step)}@{PINS[action(step)]}"):
                found.append(f"{name} uses {step['uses']}, not one of the pinned actions")
            if action(step) == "actions/checkout" and (step.get("with") or {}) != dev_rules.CHECKOUT:
                found.append(f"{name} checks out something other than the default branch's commit without credentials")
            block = step.get("run", "")
            if "${{" in block:
                found.append(f"a run block of {name} interpolates an expression (values must come through env:)")
            if re.search(r"\baws\s", block) or re.search(r"\bgh\s", block):
                found.append(f"a run block of {name} calls aws or gh directly (only the scripts may)")
    if set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) != {"AWS_DEPLOY_ROLE_ARN", "AWS_DEPLOY_ROLE_ID"}:
        found.append("the workflow reads secrets other than AWS_DEPLOY_ROLE_ARN and AWS_DEPLOY_ROLE_ID")
    if set(re.findall(r"vars\.([A-Za-z0-9_]+)", text)) != {"DEPLOY_INSTANCE_ID", "DEPLOY_URL"}:
        found.append("the workflow reads variables other than DEPLOY_INSTANCE_ID and DEPLOY_URL")
    if "secrets." in str(resolve) or "vars." in str(resolve):
        found.append("the resolve job reads a secret or a variable")
    found += resolve_problems(resolve)
    found += promote_problems(promote)
    return found


def resolve_problems(resolve):
    script = [s for s in steps(resolve) if "scripts/ci_promote.py resolve" in s.get("run", "")]
    if len(script) != 1:
        return ["the resolve job does not decide with scripts/ci_promote.py resolve"]
    found = []
    run = script[0]["run"]
    if '--output "$GITHUB_OUTPUT"' not in run or '--input-digest "$INPUT_DIGEST"' not in run:
        found.append("the resolve step does not pass the input through env and write to GITHUB_OUTPUT")
    if (script[0].get("env") or {}) != RESOLVE_ENV:
        found.append("the resolve step's environment is not the token, the repository and the input")
    expected = {key: f"${{{{ steps.{script[0].get('id')}.outputs.{key} }}}}" for key in ("image", "tag", "digest", "revision", "previous")}
    if resolve.get("outputs") != expected:
        found.append("the resolve job's outputs are not exactly the five values of its script step")
    return found


def promote_problems(promote):
    found = []
    all_steps = steps(promote)
    names = [action(s) or s.get("run", "") for s in all_steps]
    try:
        check = next(i for i, s in enumerate(all_steps) if s.get("run", "").strip() == "python3 scripts/ci_deploy.py check")
        mask = next(i for i, s in enumerate(all_steps) if "::add-mask::" in s.get("run", ""))
        role = names.index("aws-actions/configure-aws-credentials")
        send = next(i for i, s in enumerate(all_steps) if "scripts/ci_promote.py deploy" in s.get("run", ""))
        upload = names.index("actions/upload-artifact")
    except (StopIteration, ValueError):
        return ["the promote job lacks the check, mask, role, deploy or upload step"]
    if not check < mask < role < send < upload == len(names) - 1:
        found.append("the promote job's steps are not check, mask, role, deploy, then the record upload last")
    if {key: all_steps[mask].get(key) for key in ("env", "run")} != MASK:
        found.append("the mask step does not register exactly AWS_DEPLOY_ROLE_ID")
    credentials = all_steps[role].get("with") or {}
    if credentials.get("role-to-assume") != "${{ secrets.AWS_DEPLOY_ROLE_ARN }}" or credentials.get("aws-region") != "us-east-1":
        found.append("the role is not AWS_DEPLOY_ROLE_ARN in us-east-1")
    if "github.run_id" not in str(credentials.get("role-session-name", "")):
        found.append("the role session name does not include the run id")
    if {"aws-access-key-id", "aws-secret-access-key", "aws-session-token"} & set(credentials):
        found.append("the role step is given a stored key")
    if (all_steps[check].get("env") or {}) != {"DEPLOY_INSTANCE_ID": "${{ vars.DEPLOY_INSTANCE_ID }}", "DEPLOY_URL": "${{ vars.DEPLOY_URL }}"}:
        found.append("the check step does not read exactly the two Environment variables")
    if (all_steps[send].get("env") or {}) != PROMOTE_ENV:
        found.append("the deploy step's environment is not the token, the actor, the two variables, the resolve outputs and the run link")
    for flag in ("--repository", "--image", "--tag", "--digest", "--revision", "--previous", "--run-id", "--run-url", "--record"):
        if flag not in all_steps[send]["run"]:
            found.append(f"the deploy step does not pass {flag}")
    if (all_steps[upload].get("with") or {}) != RECORD:
        found.append("the record is not uploaded as deploy-record-prod, kept 90 days, overwritten on a re-run, failing when missing")
    if "if" in all_steps[upload]:
        found.append("the record upload has its own if (it must run only after every step passed)")
    return found


def test_the_real_workflow_passes_every_rule():
    doc, text = load()
    assert problems(doc, text) == []


def test_the_workflow_cannot_run_on_a_push_so_the_publish_lists_are_unchanged():
    doc, _ = load()
    assert workflow_triggers.classify(doc) == (False, False)


def broken(change):
    doc, text = load()
    doc = copy.deepcopy(doc)
    text = change(doc, text) or text
    return problems(doc, text)


def on(doc):
    return doc[True] if True in doc else doc["on"]


def job_step(doc, job, needle):
    return next(s for s in doc["jobs"][job]["steps"] if needle in str(s.get("run", s.get("uses", ""))))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda doc, text: on(doc).update(push={"branches": ["main"]}), "not workflow_dispatch only"),
        (lambda doc, text: on(doc).update(workflow_run={"workflows": ["socboard deploy dev"]}), "not workflow_dispatch only"),
        (lambda doc, text: on(doc)["workflow_dispatch"]["inputs"].update(environment={"type": "string"}), "inputs are not exactly digest"),
        (lambda doc, text: on(doc)["workflow_dispatch"]["inputs"]["digest"].update(required=True), "optional string"),
        (lambda doc, text: doc["jobs"]["resolve"].pop("if"), "resolve job does not run only on main"),
        (lambda doc, text: doc["jobs"]["promote"].update({"if": "always()"}), "promote job does not run only on main"),
        (lambda doc, text: doc["jobs"]["resolve"].update(environment="deploy-prod"), "names an Environment"),
        (lambda doc, text: doc["jobs"]["resolve"]["permissions"].update({"id-token": "write"}), "resolve job's permissions"),
        (lambda doc, text: doc["jobs"]["promote"].update(environment="deploy"), "not in the deploy-prod Environment"),
        (lambda doc, text: doc["jobs"]["promote"].pop("environment"), "not in the deploy-prod Environment"),
        (lambda doc, text: doc["jobs"]["promote"].pop("needs"), "does not need resolve"),
        (lambda doc, text: doc["jobs"]["promote"]["permissions"].update(contents="write"), "promote job's permissions"),
        (lambda doc, text: doc["jobs"]["promote"]["concurrency"].update({"cancel-in-progress": True}), "concurrency"),
        (lambda doc, text: doc["jobs"].update(extra={"runs-on": "ubuntu-latest", "steps": []}), "not resolve and promote"),
        (lambda doc, text: job_step(doc, "promote", "actions/checkout").update(uses="actions/checkout@v7"), "not one of the pinned actions"),
        (lambda doc, text: job_step(doc, "promote", "actions/checkout").update({"with": {"ref": "${{ github.sha }}"}}), "default branch's commit"),
        (lambda doc, text: job_step(doc, "resolve", "ci_promote.py resolve").update(run='python3 scripts/ci_promote.py resolve --repository "$REPOSITORY" --input-digest "${{ inputs.digest }}" --output "$GITHUB_OUTPUT"'), "interpolates an expression"),
        (lambda doc, text: job_step(doc, "promote", "ci_promote.py deploy").update({"continue-on-error": True}), "continue-on-error"),
        (lambda doc, text: doc["jobs"]["promote"]["steps"].insert(0, {"run": "aws sts get-caller-identity"}), "calls aws or gh directly"),
        (lambda doc, text: text + "\n# ${{ secrets.OTHER }}\n", "reads secrets other than"),
        (lambda doc, text: text.replace("secrets.AWS_DEPLOY_ROLE_ID", "secrets.AWS_PLAN_ROLE_ID"), "reads secrets other than"),
        (lambda doc, text: text + "\n# ${{ vars.OTHER }}\n", "variables other than"),
        (lambda doc, text: doc["jobs"]["promote"]["steps"].pop(2), "lacks the check, mask, role, deploy or upload step"),
        (lambda doc, text: doc["jobs"]["promote"]["steps"].insert(1, doc["jobs"]["promote"]["steps"].pop(3)), "not check, mask, role, deploy"),
        (lambda doc, text: job_step(doc, "promote", "::add-mask::")["env"].update(AWS_DEPLOY_ROLE_ID="${{ secrets.AWS_DEPLOY_ROLE_ARN }}"), "mask step"),
        (lambda doc, text: job_step(doc, "promote", "configure-aws-credentials")["with"].update({"aws-region": "eu-west-1"}), "us-east-1"),
        (lambda doc, text: job_step(doc, "promote", "configure-aws-credentials")["with"].update({"aws-secret-access-key": "x"}), "stored key"),
        (lambda doc, text: job_step(doc, "promote", "ci_promote.py deploy")["env"].update(EXTRA="${{ github.event.inputs.digest }}"), "deploy step's environment"),
        (lambda doc, text: job_step(doc, "promote", "upload-artifact")["with"].update(name="deploy-record-dev"), "uploaded as deploy-record-prod"),
        (lambda doc, text: job_step(doc, "promote", "upload-artifact").update({"if": "always()"}), "has its own if"),
        (lambda doc, text: job_step(doc, "resolve", "ci_promote.py resolve")["env"].update(INPUT_DIGEST="${{ github.event.inputs.digest }} x"), "resolve step's environment"),
        (lambda doc, text: doc["jobs"]["resolve"]["outputs"].update(extra="${{ steps.choose.outputs.extra }}"), "five values"),
    ],
)
def test_each_rule_rejects_a_broken_copy(change, expected):
    found = broken(change)
    assert any(expected in item for item in found), found


def test_exactly_one_of_the_real_file_and_the_proposal_exists():
    assert REAL.exists() != PROPOSED.exists()


def test_the_pins_are_the_dev_deploy_workflows_pins():
    _, text = load()
    used = dict(re.findall(r"uses: ([\w./-]+)@([0-9a-f]{40})", text))
    assert used and all(PINS[name] == sha for name, sha in used.items())
