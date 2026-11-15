"""The workflow trigger rule and the Terraform plan workflow (#209), checked from their text.

Rule: no workflow triggers on pull_request_target, and a workflow may grant `id-token: write` (workflow or job
level) only if its triggers are exactly pull_request. A pull_request_target run carries the same
`:pull_request` OIDC subject the plan role trusts, runs with the base repository's rights and can be started
by a fork. Each rule has refused fixtures (text here, not files in .github/) and a replay over the real files.
The plan workflow also masks the plan role's unique ID before the credentials step, and its log-check job fails
when the plan job's log still holds an AROA... ID; the log-check script is run here under bash with a fake gh.
"""

import copy
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PLAN = "terraform-plan.yml"
FAIL_MESSAGE = "terraform plan failed; reproduce it locally with the server plan step in terraform/README.md"
FAIL_LINE = f'fail() {{ echo "{FAIL_MESSAGE}"; exit 1; }}'
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
TERRAFORM_WORD = re.compile(r"(?<![\w.$-])terraform(?![\w/.-])")
FORK_CONDITION = "github.event.pull_request.head.repo.full_name == github.repository"
ROLE_ID_SECRET = "${{ secrets.AWS_PLAN_ROLE_ID }}"
MASK_LINE = 'echo "::add-mask::$ROLE_ID"'
LOG_CHECK_ENV = {
    "GH_TOKEN": "${{ github.token }}",
    "REPO": "${{ github.repository }}",
    "RUN_ID": "${{ github.run_id }}",
    "RUN_ATTEMPT": "${{ github.run_attempt }}",
}
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
    if sorted(jobs) != ["log-check", "plan"]:
        return found + [f"jobs are {sorted(jobs)}, not plan and log-check"]
    found += log_check_problems(jobs["log-check"])
    job = jobs["plan"]
    if job.get("permissions") != {"id-token": "write", "contents": "read"}:
        found.append("the plan job's permissions are not id-token: write and contents: read")
    if job.get("if") != FORK_CONDITION:
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
    if [v for v in string_values(doc) if "secrets.AWS_PLAN_ROLE_ID" in v] != [ROLE_ID_SECRET]:
        found.append("the role unique ID secret appears somewhere other than the mask step's env")
    found += mask_step_problems(steps, creds)
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


def mask_step_problems(steps, creds):
    """The step right before the credentials step masks the role's unique ID and never prints it."""
    if len(creds) != 1 or steps.index(creds[0]) == 0:
        return ["no mask step before the credentials step"]
    step = steps[steps.index(creds[0]) - 1]
    run = step.get("run", "")
    found = []
    if "uses" in step or step.get("env") != {"ROLE_ID": ROLE_ID_SECRET}:
        found.append("the step before the credentials step is not the mask step with env ROLE_ID from the secret")
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    if lines.count(MASK_LINE) != 1:
        found.append("the mask step has no add-mask line for ROLE_ID")
    if not any(line.startswith('if [ -z "$ROLE_ID" ]') for line in lines) or "exit 1" not in lines:
        found.append("the mask step does not fail when ROLE_ID is empty")
    for line in lines:
        if "ROLE_ID" in line and line not in (MASK_LINE, 'if [ -z "$ROLE_ID" ]; then'):
            found.append(f"the mask step uses ROLE_ID in an unexpected line: {line}")
        if line.startswith("echo") and line != MASK_LINE and "$" in line:
            found.append(f"the mask step echoes a variable: {line}")
    if MASK_LINE in lines and lines[-1] != MASK_LINE:
        found.append("the add-mask line is not the last line of the mask step")
    return found


def log_check_problems(job):
    """The log-check job holds only actions: read, no secret, and prints only fixed lines."""
    found = []
    if job.get("needs") not in ("plan", ["plan"]):
        found.append("log-check does not need plan")
    if job.get("if") != f"always() && {FORK_CONDITION}":
        found.append("log-check has no always() with the fork condition")
    if job.get("permissions") != {"actions": "read"}:
        found.append("log-check permissions are not exactly actions: read")
    if not isinstance(job.get("timeout-minutes"), int) or job["timeout-minutes"] > 10:
        found.append("log-check has no short timeout")
    if any("secrets." in v for v in string_values(job)):
        found.append("log-check references a secret")
    steps = job.get("steps") or []
    if len(steps) != 1 or any("uses" in s for s in steps):
        return found + ["log-check is not one run step with no action (no checkout, no upload)"]
    step = steps[0]
    if step.get("env") != LOG_CHECK_ENV:
        found.append("log-check env is not GH_TOKEN, REPO, RUN_ID and RUN_ATTEMPT from the github context")
    run = step.get("run", "")
    for needle in ('"repos/$REPO/actions/runs/$RUN_ID/attempts/$RUN_ATTEMPT/jobs"', '"repos/$REPO/actions/jobs/$job_id/logs"', 'log="$RUNNER_TEMP/', "rm -f \"$log\"", "'.jobs[] | select(.name == \"plan\") | .id'"):
        if needle not in run:
            found.append(f"log-check run lacks {needle}")
    lines = [line.strip() for line in run.splitlines() if line.strip()]
    if "grep -Eq 'AROA[A-Z0-9]+' \"$log\"" not in lines:
        found.append("log-check does not run grep -Eq 'AROA[A-Z0-9]+' on the log")
    for line in lines:
        for arg in re.findall(r'\becho\s+("[^"]*"|\S+)', line):
            if re.search(r"[$`]", arg):
                found.append(f"log-check echoes something that is not fixed text: {line}")
        bare = re.sub(r'\becho\s+"[^"]*"', "echo", line)
        if re.search(r"\bgrep\b", bare) and not re.search(r"\bgrep -Eq 'AROA\[A-Z0-9\]\+' \"\$log\"", bare):
            found.append(f"log-check greps in a shape that may print: {line}")
        if re.search(r"\b(cat|printf|tee|head|tail|less|more|sed|awk|cut|xargs|base64|od|xxd)\b", bare) or re.search(r"set -\w*x|GITHUB_STEP_SUMMARY|>&", bare):
            found.append(f"log-check prints or copies the log: {line}")
        for word in FORBIDDEN_IN_RUN:
            if word in bare:
                found.append(f"log-check runs {word!r}")
        if re.search(r"\bgh\b", bare):
            to_file = '> "$log" 2> /dev/null' in bare
            to_var = bare.startswith("job_id=$(gh ") and "2> /dev/null)" in bare
            if not (to_file or to_var):
                found.append(f"log-check runs gh in a shape that may print: {line}")
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


def creds_step(doc):
    return next(s for s in doc["jobs"]["plan"]["steps"] if s.get("uses", "").startswith("aws-actions/configure-aws-credentials@"))


def mask_step(doc):
    return next(s for s in doc["jobs"]["plan"]["steps"] if "ROLE_ID" in s.get("run", ""))


def swap_mask_and_credentials(doc):
    steps = doc["jobs"]["plan"]["steps"]
    mask, creds = mask_step(doc), creds_step(doc)
    i, j = steps.index(mask), steps.index(creds)
    steps[i], steps[j] = creds, mask


def mask_edit(old, new):
    def change(doc):
        step = mask_step(doc)
        assert old in step["run"], old
        step["run"] = step["run"].replace(old, new)

    return change


def lc_step(doc):
    return doc["jobs"]["log-check"]["steps"][0]


def lc_edit(old, new):
    def change(doc):
        step = lc_step(doc)
        assert old in step["run"], old
        step["run"] = step["run"].replace(old, new)

    return change


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
    "role from a variable": lambda doc: creds_step(doc)["with"].update({"role-to-assume": "${{ vars.AWS_PLAN_ROLE_ARN }}"}),
    "role in the summary": add_step({"run": 'echo "${{ secrets.AWS_PLAN_ROLE_ARN }}" >> "$GITHUB_STEP_SUMMARY"'}),
    "plan uploaded": add_step({"uses": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", "with": {"path": "plan"}}),
    "show printed by another step": add_step({"run": 'terraform -chdir=terraform/server show "$RUNNER_TEMP/server.tfplan"'}),
    "show printed through env": add_step({"run": 'env terraform -chdir=terraform/server show "$RUNNER_TEMP/server.tfplan"'}),
    "show by absolute path in another step": add_step(
        {"run": '/usr/bin/terraform -chdir=terraform/server show -json "$RUNNER_TEMP/server.tfplan"'}
    ),
    "show by absolute path": edit_run("terraform -chdir=terraform/server show -json", "/usr/bin/terraform -chdir=terraform/server show -json"),
    "plan by absolute path": edit_run("terraform -chdir=terraform/server plan", "/usr/local/bin/terraform -chdir=terraform/server plan"),
    "action by tag": lambda doc: creds_step(doc).update({"uses": "aws-actions/configure-aws-credentials@v6"}),
    "no cleanup": lambda doc: doc["jobs"]["plan"]["steps"].pop(),
    # The mask step (A).
    "mask step missing": lambda doc: doc["jobs"]["plan"]["steps"].remove(mask_step(doc)),
    "mask after credentials": lambda doc: swap_mask_and_credentials(doc),
    "mask step without the secret env": lambda doc: mask_step(doc).update({"env": {"ROLE_ID": "x"}}),
    "mask step from the ARN secret": lambda doc: mask_step(doc).update({"env": {"ROLE_ID": "${{ secrets.AWS_PLAN_ROLE_ARN }}"}}),
    "mask step without the empty check": lambda doc: mask_edit('if [ -z "$ROLE_ID" ]; then', 'if [ -n "$ROLE_ID" ]; then')(doc),
    "mask step without add-mask": lambda doc: mask_edit(MASK_LINE, "true")(doc),
    "mask step prints the id": lambda doc: mask_edit(MASK_LINE, 'echo "$ROLE_ID"\n' + MASK_LINE)(doc),
    "mask step error shows the id": lambda doc: mask_edit("::error::the plan role unique ID secret", "::error::$ROLE_ID")(doc),
    "id secret used elsewhere": add_step({"run": "true", "env": {"X": "${{ secrets.AWS_PLAN_ROLE_ID }}"}}),
    "id secret in the plan step": lambda doc: plan_step(doc).update({"env": {"X": "${{ secrets.AWS_PLAN_ROLE_ID }}"}}),
    # The log-check job (B).
    "no log-check job": lambda doc: doc["jobs"].pop("log-check"),
    "extra job": lambda doc: doc["jobs"].update({"other": {"runs-on": "ubuntu-latest", "steps": []}}),
    "log-check without needs": lambda doc: doc["jobs"]["log-check"].pop("needs"),
    "log-check needs another job": set_key(["jobs", "log-check", "needs"], "other"),
    "log-check without always": set_key(["jobs", "log-check", "if"], FORK_CONDITION),
    "log-check always without the fork condition": set_key(["jobs", "log-check", "if"], "always()"),
    "log-check no timeout": lambda doc: doc["jobs"]["log-check"].pop("timeout-minutes"),
    "log-check with contents": set_key(["jobs", "log-check", "permissions", "contents"], "read"),
    "log-check with pull-requests write": set_key(["jobs", "log-check", "permissions", "pull-requests"], "write"),
    "log-check with id-token": set_key(["jobs", "log-check", "permissions", "id-token"], "write"),
    "log-check without permissions": lambda doc: doc["jobs"]["log-check"].pop("permissions"),
    "log-check actions write": set_key(["jobs", "log-check", "permissions", "actions"], "write"),
    "log-check with a secret": lambda doc: lc_step(doc)["env"].update({"S": "${{ secrets.OTHER }}"}),
    "log-check with checkout": lambda doc: doc["jobs"]["log-check"]["steps"].insert(0, {"uses": "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"}),
    "log-check uploads": lambda doc: doc["jobs"]["log-check"]["steps"].append({"uses": "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"}),
    "log-check with a second run step": lambda doc: doc["jobs"]["log-check"]["steps"].append({"run": "true"}),
    "log-check token from elsewhere": lambda doc: lc_step(doc)["env"].update({"GH_TOKEN": "${{ secrets.PAT }}"}),
    "log-check grep without q": lc_edit("grep -Eq", "grep -E"),
    "log-check grep with another flag": lc_edit("grep -Eq 'AROA", "grep -Eqo 'AROA"),
    "log-check extra grep that prints": lc_edit('rm -f "$log"\necho "log check passed', 'grep AROA "$log"\necho "log check passed'),
    "log-check other pattern": lc_edit("AROA[A-Z0-9]+", "ASIA[A-Z0-9]+"),
    "log-check cats the log": lc_edit("set +e\n", 'cat "$log"\n          set +e\n'),
    "log-check echoes the log": lc_edit("set +e\n", 'echo "$(head -c 50 "$log")"\n          set +e\n'),
    "log-check echoes a variable": lc_edit('echo "log check passed:', 'echo "$job_id log check passed:'),
    "log-check echoes the match": lc_edit('grep -Eq', 'echo "$(grep -Eo \'AROA[A-Z0-9]+\' "$log")"\n          grep -Eq'),
    "log-check keeps gh stderr": lc_edit('/logs" > "$log" 2> /dev/null', '/logs" > "$log"'),
    "log-check keeps gh stderr on lookup": lc_edit("| .id' 2> /dev/null)", "| .id')"),
    "log-check gh to stdout": lc_edit('/logs" > "$log" 2> /dev/null', '/logs" 2> /dev/null'),
    "log-check never deletes the log": lc_edit('rm -f "$log"', "true"),
    "log-check log outside the runner temp": lc_edit('log="$RUNNER_TEMP/', 'log="plan-job.log" # '),
    "log-check traces": lc_edit("fail() {", "set -x\n          fail() {"),
    "log-check writes the summary": lc_edit("set +e\n", 'echo hi >> "$GITHUB_STEP_SUMMARY"\n          set +e\n'),
}


@pytest.mark.parametrize("case", sorted(BROKEN))
def test_a_broken_plan_workflow_is_refused(case):
    doc = copy.deepcopy(load_plan())
    BROKEN[case](doc)
    assert plan_problems(doc), case


@pytest.mark.parametrize("case", ["show by absolute path", "plan by absolute path", "show by absolute path in another step", "show printed through env"])
def test_a_terraform_path_or_env_prefix_is_seen_as_the_command(case):
    doc = copy.deepcopy(load_plan())
    BROKEN[case](doc)
    assert any("runs terraform in a shape that may print" in p for p in plan_problems(doc)), case


@pytest.mark.parametrize(
    "text",
    [
        "terraform plan",
        "  terraform -chdir=terraform/server show",
        "env terraform plan",
        "/usr/bin/terraform plan",
        "/usr/local/bin/terraform -chdir=terraform/server show -json",
        "./terraform plan",
        "x && terraform plan",
        "(terraform plan)",
    ],
)
def test_terraform_word_matches_the_command(text):
    assert TERRAFORM_WORD.search(text), text


@pytest.mark.parametrize(
    "text",
    [
        "-chdir=terraform/server",
        "cat terraform/server/main.tf",
        "terraform/.terraform-version",
        "tr -d x < .terraform-version",
        "$terraform plan",
        "my-terraform plan",
        "terraformer plan",
        "terraform.tfstate",
        "echo no command here",
    ],
)
def test_terraform_word_ignores_names_and_folders(text):
    assert not TERRAFORM_WORD.search(text), text


# The log-check step's script, run under bash with a fake `gh` first on PATH.

FAKE_GH = """#!/bin/bash
# Fake gh: FAKE_JOB_ID answers the job lookup, FAKE_LOG_FILE the log download; FAKE_FAIL names the call that fails.
case "$*" in
  *attempts*)
    if [ "$FAKE_FAIL" = jobs ]; then echo "fake gh error AROAERRORTEXT1" >&2; exit 1; fi
    echo "$FAKE_JOB_ID"
    ;;
  *logs*)
    if [ "$FAKE_FAIL" = logs ]; then echo "fake gh error AROAERRORTEXT2" >&2; exit 1; fi
    cat "$FAKE_LOG_FILE"
    ;;
  *)
    exit 3
    ;;
esac
"""


def run_log_check(tmp_path, log_text, job_id="4242", fail=""):
    """Run the real log-check step text; returns (exit code, combined output, the temp dir)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    log_file = tmp_path / "fake-log.txt"
    log_file.write_text(log_text, encoding="utf-8")
    script = tmp_path / "step.sh"
    script.write_text(lc_step(load_plan())["run"], encoding="utf-8")
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RUNNER_TEMP": str(temp),
        "GH_TOKEN": "fake-token",
        "REPO": "owner/repo",
        "RUN_ID": "1",
        "RUN_ATTEMPT": "1",
        "FAKE_JOB_ID": job_id,
        "FAKE_LOG_FILE": str(log_file),
        "FAKE_FAIL": fail,
    }
    done = subprocess.run(["bash", "-eo", "pipefail", str(script)], env=env, capture_output=True, text=True, check=False, timeout=30)
    return done.returncode, done.stdout + done.stderr, temp


CLEAN_LOG = "Run terraform plan\nAuthenticated as assumedRoleId ***\nPlan: 0 to add, 0 to change, 0 to destroy.\n"


def test_log_check_passes_a_clean_log(tmp_path):
    code, output, temp = run_log_check(tmp_path, CLEAN_LOG)
    assert code == 0
    assert "passed" in output
    assert list(temp.iterdir()) == []


def test_log_check_passes_a_masked_log(tmp_path):
    code, output, temp = run_log_check(tmp_path, "Authenticated as assumedRoleId ***\n")
    assert code == 0
    assert list(temp.iterdir()) == []


def test_log_check_fails_on_a_role_id_and_prints_none_of_it(tmp_path):
    code, output, temp = run_log_check(tmp_path, CLEAN_LOG + "Authenticated as assumedRoleId AROAEXAMPLE123\n")
    assert code != 0
    assert "FAILED" in output
    assert "AROA" not in output and "EXAMPLE123" not in output and "Plan:" not in output
    assert list(temp.iterdir()) == []


@pytest.mark.parametrize("fail", ["jobs", "logs"])
def test_log_check_fails_when_gh_fails_and_prints_no_gh_text(tmp_path, fail):
    code, output, temp = run_log_check(tmp_path, CLEAN_LOG, fail=fail)
    assert code != 0
    assert "could not be checked" in output
    assert "AROA" not in output and "fake gh" not in output
    assert list(temp.iterdir()) == []


def test_log_check_fails_on_an_empty_log(tmp_path):
    code, output, temp = run_log_check(tmp_path, "")
    assert code != 0
    assert "could not be checked" in output
    assert list(temp.iterdir()) == []


@pytest.mark.parametrize("job_id", ["", "abc", "1\n2"])
def test_log_check_fails_when_the_plan_job_is_not_found_once(tmp_path, job_id):
    code, output, temp = run_log_check(tmp_path, CLEAN_LOG, job_id=job_id)
    assert code != 0
    assert "could not be checked" in output
    assert list(temp.iterdir()) == []
