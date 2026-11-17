"""The proposed `socboard observability` workflow (#340), checked from its text.

The file lives at observability/ci/proposed/observability.yml until the CI lane or the maintainer lands it
as .github/workflows/observability.yml (a protected path). Nothing here runs Actions. The tests also say
how its triggers read in tests/workflow_triggers.py, which is the answer the issue asks the CI lane to
record: the workflow can run on a push to main (so publish.yml must list it) and does not run on every
push (so the gate's REQUIRED list must not).
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import workflow_triggers
import yaml

ROOT = Path(__file__).resolve().parents[1]
PROPOSED = ROOT / "observability" / "ci" / "proposed" / "observability.yml"
WORKFLOWS = ROOT / ".github" / "workflows"
NAME = "socboard observability"
BROWSER_INSTALL = "make e2e-install\ncd e2e && bunx playwright install --with-deps chromium"
REQUIRED_PATHS = {
    "observability/**",
    "compose.observability.yaml",
    "socboard/telemetry.py",
    "socboard/ingest.py",
    "socboard/cases.py",
    "scripts/observability_*",
}
spec = importlib.util.spec_from_file_location("publish_gate", ROOT / "scripts" / "publish_gate.py")
gate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate_module)


def load(path=PROPOSED):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def on(doc):
    return doc.get("on", doc.get(True)) or {}  # PyYAML reads the key `on` as True


def problems(doc):
    """What is wrong with a candidate workflow (a parsed file, so comments cannot trip it), as lines."""
    found = []
    blob = json.dumps(doc, default=str)
    if doc.get("name") != NAME:
        found.append(f"the name is {doc.get('name')!r}, not {NAME!r}")
    if doc.get("permissions") != {"contents": "read"}:
        found.append("the default permissions are not contents: read")
    if "pull_request_target" in blob:
        found.append("it uses pull_request_target")
    if "secrets." in blob or '"secrets"' in blob:
        found.append("it names a secret")
    if '"write"' in blob:
        found.append("it names a write permission")
    if '"environment"' in blob:
        found.append("it names an environment")
    if "id-token" in blob:
        found.append("it asks for an OIDC token (a cloud role)")
    events = set(on(doc))
    if events != {"push", "pull_request", "workflow_dispatch", "schedule"}:
        found.append(f"the events are {sorted(events)}")
    for event in ("push", "pull_request"):
        if not REQUIRED_PATHS <= set((on(doc).get(event) or {}).get("paths") or []):
            found.append(f"{event} does not filter on every required path")
    if not (on(doc).get("schedule") or [{}])[0].get("cron"):
        found.append("there is no nightly schedule")
    jobs = doc.get("jobs") or {}
    if len(jobs) != 1:
        found.append(f"{len(jobs)} jobs, not one")
    for job in jobs.values():
        if "permissions" in job:
            found.append("a job sets its own permissions")
        runs = [step["run"].strip() for step in job.get("steps", []) if "run" in step]
        if runs != [BROWSER_INSTALL, "bash observability/ci/check.sh"]:
            found.append(f"the job runs {runs}, not the browser install and bash observability/ci/check.sh")
        uses = [step["uses"].split("@")[0] for step in job.get("steps", []) if "uses" in step]
        if uses != ["actions/checkout", "astral-sh/setup-uv", "actions/setup-node", "oven-sh/setup-bun", "actions/upload-artifact"]:
            found.append(f"the job uses {uses}, not checkout, setup-uv, setup-node, setup-bun and upload-artifact")
        envs = [step["env"] for step in job.get("steps", []) if "env" in step]
        if envs != [{"SCREENSHOT_DIR": "${{ runner.temp }}/observability-evidence"}]:
            found.append(f"the job sets {envs} in an env, not only SCREENSHOT_DIR")
        upload = [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith("actions/upload-artifact@")]
        if len(upload) != 1 or upload[0].get("if") != "always()" or upload[0]["with"].get("path") != "${{ runner.temp }}/observability-evidence":
            found.append("the artifact upload is not the one evidence directory, always")
    return found


def test_the_proposed_workflow_passes_every_rule():
    assert problems(load()) == []


def broken(change):
    doc = copy.deepcopy(load())
    change(doc)
    return problems(doc)


@pytest.mark.parametrize(
    "change",
    [
        lambda doc: on(doc).update({"pull_request_target": {}}),
        lambda doc: doc["jobs"]["observability"]["steps"].append({"run": "echo ${{ secrets.TOKEN }}"}),
        lambda doc: doc.update({"permissions": {"contents": "write"}}),
        lambda doc: doc.update({"permissions": {"contents": "read", "packages": "write"}}),
        lambda doc: doc["jobs"]["observability"].update({"permissions": {"id-token": "write"}}),
        lambda doc: doc["jobs"]["observability"].update({"environment": "deploy"}),
        lambda doc: doc["jobs"]["observability"]["steps"].append({"run": "docker build ."}),
        lambda doc: doc["jobs"]["observability"]["steps"].append({"uses": "some/action@v1"}),
        lambda doc: doc["jobs"]["observability"]["steps"][-1].update({"if": "success()"}),
        lambda doc: doc["jobs"]["observability"]["steps"][-1]["with"].update({"path": "."}),
        lambda doc: doc["jobs"].update({"second": {"runs-on": "ubuntu-latest", "steps": []}}),
        lambda doc: on(doc).pop("schedule") and None,
        lambda doc: on(doc).pop("workflow_dispatch") and None,
        lambda doc: on(doc)["push"].update({"paths": ["observability/**"]}),
        lambda doc: on(doc)["push"].update({"paths": ["observability/**", "bogus/**"]}),
        lambda doc: doc.update({"name": "observability"}),
    ],
    ids=["pull_request_target", "a secret", "contents write", "packages write", "an OIDC token", "an environment", "an extra step", "an extra action", "an upload only on success", "an upload of the whole checkout", "a second job", "no schedule", "no manual run", "a short path list", "a wrong path in place of the rest", "another name"],
)
def test_each_refused_case_fails(change):
    assert broken(change), "the change was not caught"


def real_workflows():
    found = {}
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
        found[f".github/workflows/{path.name}"] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return found


OWN = ".github/workflows/publish.yml"
LANDED = ".github/workflows/observability.yml"


def test_the_workflow_can_run_on_a_push_to_main_but_not_on_every_push():
    doc = load()
    assert workflow_triggers.classify(doc) == (True, False)
    assert workflow_triggers.workflow_name(doc, LANDED) == NAME


def test_once_landed_it_needs_the_publish_trigger_list_but_not_the_required_list():
    """The answer for the CI lane: list it in publish.yml; do not add it to the gate's REQUIRED."""
    doc = load()
    workflows = {**real_workflows(), LANDED: doc}
    publish = real_workflows()[OWN]
    listed = set(on(publish)["workflow_run"]["workflows"])
    # Without its name in publish.yml's list it is a problem; with it, none. `listed - {NAME}` keeps this
    # true after the landing edit to publish.yml, which adds the name.
    assert workflow_triggers.list_problems(workflows, listed - {NAME}, OWN) != []
    assert workflow_triggers.list_problems(workflows, listed | {NAME}, OWN) == []
    assert workflow_triggers.required_problems(workflows, set(gate_module.REQUIRED), OWN) == []  # not required


def gate_runs(*extra):
    sha, repo = "0123456789abcdef0123456789abcdef01234567", "andrew-chung-au/socboard"

    def run(name, number, conclusion="success", status="completed"):
        return {"id": number, "name": name, "status": status, "conclusion": conclusion, "head_sha": sha, "event": "push", "head_branch": "main", "head_repository": {"full_name": repo}}

    required = [run(name, 100 + number) for number, name in enumerate(gate_module.REQUIRED)]
    decision = lambda runs: gate_module.decide(repository=repo, sha=sha, event="push", branch="main", head_repository=repo, runs=runs)[:2]  # noqa: E731
    return decision, required, run


def test_a_failed_run_of_it_on_a_commit_holds_the_publish_back_and_no_run_holds_nothing():
    decision, required, run = gate_runs()
    assert decision(required)[0] is True  # a commit that touched none of its paths has no such run
    assert decision([*required, run(NAME, 300)])[0] is True
    ready, reason = decision([*required, run(NAME, 300, conclusion="failure")])
    assert not ready and f"{NAME} (failure)" in reason
    ready, reason = decision([*required, run(NAME, 300, conclusion=None, status="in_progress")])
    assert not ready and f"{NAME} still running" in reason
