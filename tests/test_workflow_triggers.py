"""The workflow trigger rule and the Terraform plan workflow (#209), checked from their text.

Rule: no workflow triggers on pull_request_target, and a workflow may grant `id-token: write` (workflow or job
level) only if its triggers are exactly pull_request. A pull_request_target run carries the same
`:pull_request` OIDC subject the plan role trusts, runs with the base repository's rights and can be started
by a fork. Each rule has refused fixtures (text here, not files in .github/) and a replay over the real files.
"""

import copy
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PLAN = "terraform-plan.yml"
FAIL_MESSAGE = "terraform plan failed; reproduce it locally with the server plan step in terraform/README.md"
FAIL_LINE = f'fail() {{ echo "{FAIL_MESSAGE}"; exit 1; }}'
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
TERRAFORM_WORD = re.compile(r"(?<![\w/.$-])terraform(?![\w/.-])")
CHDIR = "terraform -chdir=terraform/server "
PLAN_FILE = '"$RUNNER_TEMP/server.tfplan"'
ALLOWED_TERRAFORM_LINES = {
    "init": re.compile(
        re.escape(CHDIR + 'init -input=false -backend-config="bucket=${{ vars.TF_STATE_BUCKET }}" > /dev/null 2>&1 || fail')
    ),
    "plan": re.compile(
        re.escape(CHDIR + f"plan -input=false -lock=false -no-color -out={PLAN_FILE} > /dev/null 2>&1 || fail")
    ),
    "show": re.compile(re.escape(CHDIR + f"show -json {PLAN_FILE} 2> /dev/null | python3 -I scripts/check_plan_actions.py")),
}
FORBIDDEN_IN_RUN = ("apply", "destroy", "import", "force-unlock", "state ", "-lock=true", "tee", "upload")


def workflow_files():
    return sorted(p for p in WORKFLOWS.iterdir() if p.suffix in (".yml", ".yaml"))


def trigger_names(doc):
    """The workflow's trigger names, or None for a form this rule does not know."""
    on = doc.get("on", doc.get(True))  # PyYAML (YAML 1.1) reads the key `on` as True
    if isinstance(on, str):
        return [on]
    if isinstance(on, list) and on and all(isinstance(t, str) for t in on):
        return on
    if isinstance(on, dict) and on and all(isinstance(t, str) for t in on):
        return list(on)
    return None


def grants_id_token(permissions):
    """True, False, or None for a form this rule does not know."""
    if permissions is None or permissions in ("read-all", {}):
        return False
    if permissions == "write-all":
        return True
    if isinstance(permissions, dict):
        return permissions.get("id-token") == "write"
    return None


def rule_problems(name, text):
    """Problems with one workflow file under the trigger rule; [] means it passes."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return [f"{name}: not parsable YAML"]
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return [f"{name}: not a workflow with jobs"]
    triggers = trigger_names(doc)
    if triggers is None:
        return [f"{name}: triggers in a form the rule does not know"]
    found = []
    if "pull_request_target" in triggers:
        found.append(f"{name}: triggers on pull_request_target")
    grants = [grants_id_token(doc.get("permissions"))]
    grants += [grants_id_token(job.get("permissions")) if isinstance(job, dict) else None for job in doc["jobs"].values()]
    if None in grants:
        found.append(f"{name}: permissions in a form the rule does not know")
    elif any(grants) and triggers != ["pull_request"]:
        found.append(f"{name}: grants id-token: write with triggers {triggers}, not pull_request only")
    return found


def grants_any_id_token(text):
    doc = yaml.safe_load(text)
    perms = [doc.get("permissions")] + [job.get("permissions") for job in doc["jobs"].values()]
    return any(grants_id_token(p) for p in perms)


GOOD = """
on:
  pull_request:
    paths: ["terraform/server/**"]
permissions: {}
jobs:
  plan:
    permissions:
      id-token: write
      contents: read
    runs-on: ubuntu-latest
    steps: [{run: "true"}]
"""


@pytest.mark.parametrize(
    "text",
    [
        GOOD.replace("pull_request:", "pull_request_target:"),
        GOOD.replace("pull_request:", "pull_request_target:").replace("id-token: write", "id-token: none"),
        GOOD.replace("pull_request:", "push:"),
        GOOD.replace("on:\n  pull_request:", "on:\n  push:\n  pull_request:"),
        GOOD.replace("permissions: {}", "permissions: write-all").replace("pull_request:", "push:"),
        GOOD.replace("permissions: {}", "permissions:\n  id-token: write").replace("pull_request:", "workflow_dispatch:"),
        "on: pull_request_target\njobs: {a: {runs-on: x, steps: []}}\n",
        "on: [push, pull_request_target]\njobs: {a: {runs-on: x, steps: []}}\n",
        "on: 7\njobs: {a: {runs-on: x, steps: []}}\n",
        "jobs: {a: {runs-on: x, steps: []}}\n",
        GOOD.replace("permissions: {}", "permissions: [id-token]"),
        "on: [pull_request\njobs: :\n",
        "just text\n",
    ],
)
def test_the_rule_refuses(text):
    problems = rule_problems("fixture.yml", text)
    assert problems and all(p.startswith("fixture.yml: ") for p in problems)


@pytest.mark.parametrize(
    "text",
    [
        GOOD,
        "on: pull_request\npermissions:\n  id-token: write\njobs: {a: {runs-on: x, steps: []}}\n",
        "on: [push, pull_request]\npermissions: {contents: read}\njobs: {a: {runs-on: x, steps: []}}\n",
        GOOD.replace("id-token: write", "id-token: read").replace("pull_request:", "push:"),
    ],
)
def test_the_rule_allows(text):
    assert rule_problems("fixture.yml", text) == []


def test_replay_every_workflow_passes_and_only_the_plan_workflow_grants_id_token():
    files = workflow_files()
    assert files, "no workflow file found"
    problems = [p for f in files for p in rule_problems(f.name, f.read_text(encoding="utf-8"))]
    assert problems == []
    granting = {f.name for f in files if grants_any_id_token(f.read_text(encoding="utf-8"))}
    assert granting == {PLAN}


# The plan workflow's own rules.


def load_plan():
    return yaml.safe_load((WORKFLOWS / PLAN).read_text(encoding="utf-8"))


def string_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from string_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from string_values(value)
    elif isinstance(node, str):
        yield node


def plan_problems(doc):
    found = []
    on = doc.get("on", doc.get(True)) or {}
    if not isinstance(on, dict) or list(on) != ["pull_request"]:
        found.append("triggers are not pull_request only")
    elif set((on["pull_request"] or {}).get("paths", [])) != {"terraform/server/**", "terraform/.terraform-version", f".github/workflows/{PLAN}"}:
        found.append("the paths filter is not the server folder, the version file and this file")
    if doc.get("permissions") != {}:
        found.append("workflow permissions are not {}")
    if (doc.get("defaults") or {}).get("run", {}).get("shell") != "bash":
        found.append("scripts do not run in bash")
    jobs = doc.get("jobs") or {}
    if list(jobs) != ["plan"]:
        return found + [f"jobs are {list(jobs)}, not plan only"]
    job = jobs["plan"]
    if job.get("permissions") != {"id-token": "write", "contents": "read"}:
        found.append("the plan job's permissions are not id-token: write and contents: read")
    if job.get("if") != "github.event.pull_request.head.repo.full_name == github.repository":
        found.append("the plan job has no fork condition")
    steps = job.get("steps") or []
    uses = [s["uses"] for s in steps if "uses" in s]
    for ref in uses:
        if not PINNED.fullmatch(ref):
            found.append(f"{ref} is not pinned by commit")
        if ref.split("@")[0] in ("actions/upload-artifact", "actions/cache"):
            found.append(f"{ref} uploads or caches files")
    creds = [s for s in steps if s.get("uses", "").startswith("aws-actions/configure-aws-credentials@")]
    if len(creds) != 1:
        found.append("not exactly one AWS credentials step")
    else:
        given = creds[0].get("with") or {}
        if given.get("role-to-assume") != "${{ secrets.AWS_PLAN_ROLE_ARN }}" or given.get("aws-region") != "us-east-1":
            found.append("the credentials step does not assume secrets.AWS_PLAN_ROLE_ARN in us-east-1")
        if any(key.startswith("aws-access-key") or key.startswith("aws-secret") or key.startswith("aws-session") for key in given):
            found.append("the credentials step passes a stored credential")
    if [v for v in string_values(doc) if "AWS_PLAN_ROLE_ARN" in v] != ["${{ secrets.AWS_PLAN_ROLE_ARN }}"]:
        found.append("the role ARN appears somewhere other than the credentials step's input")
    names = [s.get("name") or s.get("uses") or f"#{i}" for i, s in enumerate(steps)]
    backend = [i for i, s in enumerate(steps) if "terraform/server/backend.tf" in s.get("run", "") and "D19a" in s.get("run", "")]
    if not backend or not creds or backend[0] > steps.index(creds[0]):
        found.append("no backend file check naming D19a before the credentials step")
    cleanup = [s for s in steps if s.get("run", "").strip() == f"rm -f {PLAN_FILE}"]
    if not cleanup or cleanup[0].get("if") != "always()":
        found.append("the plan file is not deleted in an if: always() step")
    ran = set()
    for step, name in zip(steps, names):
        run = step.get("run", "")
        if not TERRAFORM_WORD.search(run.replace(FAIL_MESSAGE, "")):
            for word in FORBIDDEN_IN_RUN:
                if word in run:
                    found.append(f"step {name!r} runs {word!r}")
            continue
        found += terraform_step_problems(name, run, ran)
    if ran != set(ALLOWED_TERRAFORM_LINES):
        found.append(f"terraform commands run: {sorted(ran)}, not init, plan and show")
    return found


def terraform_step_problems(name, run, ran):
    found = []
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    if FAIL_LINE not in lines:
        found.append(f"step {name!r} has no fixed failure message")
    for line in lines:
        if line == FAIL_LINE:
            continue
        for word in FORBIDDEN_IN_RUN:
            if word in line:
                found.append(f"step {name!r} runs {word!r}")
        if re.search(r"\b(echo|printf|cat)\b", line) or "GITHUB_STEP_SUMMARY" in line:
            found.append(f"step {name!r} prints something other than the fixed message: {line}")
        if not TERRAFORM_WORD.search(line):
            continue
        matched = [cmd for cmd, shape in ALLOWED_TERRAFORM_LINES.items() if shape.fullmatch(line)]
        if not matched:
            found.append(f"step {name!r} runs terraform in a shape that may print: {line}")
        ran.update(matched)
    return found


def test_the_plan_workflow_passes_its_rules():
    assert plan_problems(load_plan()) == []


def test_the_plan_workflow_keeps_the_terraform_check_pins():
    check = yaml.safe_load((WORKFLOWS / "terraform-check.yml").read_text(encoding="utf-8"))
    check_uses = {s["uses"] for s in check["jobs"]["check"]["steps"] if "uses" in s}
    plan_uses = {s["uses"] for s in load_plan()["jobs"]["plan"]["steps"] if "uses" in s}
    assert check_uses <= plan_uses


def test_every_pinned_action_names_its_version_in_a_comment():
    text = (WORKFLOWS / PLAN).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line), line


def plan_step(doc):
    return next(s for s in doc["jobs"]["plan"]["steps"] if "show -json" in s.get("run", ""))


def edit_run(old, new):
    def change(doc):
        step = plan_step(doc)
        assert old in step["run"]
        step["run"] = step["run"].replace(old, new)

    return change


def set_key(path, value):
    def change(doc):
        node = doc
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return change


def add_step(step):
    return lambda doc: doc["jobs"]["plan"]["steps"].insert(-1, step)


BROKEN = {
    "plan output printed": edit_run('-out="$RUNNER_TEMP/server.tfplan" > /dev/null 2>&1', '-out="$RUNNER_TEMP/server.tfplan"'),
    "init error printed": edit_run('}}" > /dev/null 2>&1', '}}" > /dev/null'),
    "plan teed": edit_run('server.tfplan" > /dev/null 2>&1 || fail', 'server.tfplan" 2>&1 | tee plan.txt > /dev/null || fail'),
    "show printed": edit_run("2> /dev/null | python3 -I scripts/check_plan_actions.py", "2> /dev/null"),
    "show stderr kept": edit_run('server.tfplan" 2> /dev/null |', 'server.tfplan" |'),
    "plan locks": edit_run("-lock=false", "-lock=true"),
    "plan without lock flag": edit_run("-lock=false ", ""),
    "apply added": edit_run("set +e\n", "terraform -chdir=terraform/server apply -auto-approve > /dev/null 2>&1 || fail\nset +e\n"),
    "state subcommand": edit_run("set +e\n", "terraform -chdir=terraform/server state list > /dev/null 2>&1 || fail\nset +e\n"),
    "failure text echoed": edit_run("2>&1 || fail\nset +e", "2>&1 || { echo oops; fail; }\nset +e"),
    "different failure message": edit_run("server plan step", "plan step"),
    "no fork condition": set_key(["jobs", "plan", "if"], "true"),
    "write token": set_key(["jobs", "plan", "permissions", "pull-requests"], "write"),
    "workflow permissions": set_key(["permissions"], {"contents": "read"}),
    "push trigger": set_key([True, "push"], {}),
    "role from a variable": lambda doc: doc["jobs"]["plan"]["steps"][4]["with"].update({"role-to-assume": "${{ vars.AWS_PLAN_ROLE_ARN }}"}),
    "role in the summary": add_step({"run": 'echo "${{ secrets.AWS_PLAN_ROLE_ARN }}" >> "$GITHUB_STEP_SUMMARY"'}),
    "plan uploaded": add_step({"uses": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", "with": {"path": "plan"}}),
    "show printed by another step": add_step({"run": 'terraform -chdir=terraform/server show "$RUNNER_TEMP/server.tfplan"'}),
    "action by tag": set_key(["jobs", "plan", "steps", 4, "uses"], "aws-actions/configure-aws-credentials@v6"),
    "no cleanup": lambda doc: doc["jobs"]["plan"]["steps"].pop(),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_broken_plan_workflow_is_refused(case):
    doc = copy.deepcopy(load_plan())
    BROKEN[case](doc)
    assert plan_problems(doc), case
