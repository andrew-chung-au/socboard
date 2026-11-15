"""tests/workflow_triggers.py (#258): which workflows GitHub runs on a push to main.

Every workflow here is a small synthetic document, parsed from YAML text the way the real files are
(so PyYAML's reading of `on` as `True` is in play). The two answers are (can run on a push to main,
runs on every push to main).
"""

import textwrap

import pytest
import workflow_triggers as triggers
import yaml

BODY = "jobs:\n  p:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo probe\n"


def doc(on_text, name="probe"):
    head = f"name: {name}\n" if name else ""
    return yaml.safe_load(head + textwrap.dedent(on_text).strip("\n") + "\n" + BODY)


CASES = [
    # (id, `on:` text, can run on a push to main, runs on every push to main)
    ("string push", "on: push", True, True),
    ("string pull_request", "on: pull_request", False, False),
    ("string workflow_dispatch", "on: workflow_dispatch", False, False),
    ("list push and pull_request", "on: [push, pull_request]", True, True),
    ("list pull_request", "on: [pull_request]", False, False),
    ("list push", "on: [push]", True, True),
    ("mapping schedule", "on:\n  schedule:\n    - cron: '0 0 * * *'", False, False),
    ("mapping workflow_run", "on:\n  workflow_run:\n    workflows: [a]\n    types: [completed]", False, False),
    ("mapping pull_request only", "on:\n  pull_request:", False, False),
    ("mapping pull_request with filters", "on:\n  pull_request:\n    paths: ['a/**']", False, False),
    ("mapping push null", "on:\n  push:", True, True),
    ("mapping push empty", "on:\n  push: {}", True, True),
    ("push and pull_request together", "on:\n  push:\n  pull_request:", True, True),
    ("branches main", "on:\n  push:\n    branches: [main]", True, True),
    ("branches double star", "on:\n  push:\n    branches: ['**']", True, True),
    ("branches star", "on:\n  push:\n    branches: ['*']", True, True),
    ("branches main and release", "on:\n  push:\n    branches: [main, 'release/*']", True, True),
    ("branches release only", "on:\n  push:\n    branches: ['release/*']", False, False),
    ("branches own probe branch", "on:\n  push:\n    branches: [oidc-probe-156]", False, False),
    ("branches a string", "on:\n  push:\n    branches: oidc-probe-156", False, False),
    ("branches ma star", "on:\n  push:\n    branches: ['ma*']", True, True),
    ("branches mai", "on:\n  push:\n    branches: ['mai']", False, False),
    ("branches everything but main", "on:\n  push:\n    branches: ['**', '!main']", False, False),
    ("branches negation then a later positive", "on:\n  push:\n    branches: ['!main', main]", True, True),
    ("branches negation of another", "on:\n  push:\n    branches: ['**', '!release/*']", True, True),
    ("branches-ignore main", "on:\n  push:\n    branches-ignore: [main]", False, False),
    ("branches-ignore other", "on:\n  push:\n    branches-ignore: ['feature/*']", True, True),
    ("branches-ignore star", "on:\n  push:\n    branches-ignore: ['*']", False, False),
    ("tags only", "on:\n  push:\n    tags: ['v*']", False, False),
    ("branches and tags", "on:\n  push:\n    branches: [main]\n    tags: ['v*']", True, True),
    ("paths", "on:\n  push:\n    paths: ['terraform/**']", True, False),
    ("paths-ignore", "on:\n  push:\n    paths-ignore: ['docs/**']", True, False),
    ("branches main and paths", "on:\n  push:\n    branches: [main]\n    paths: ['x']", True, False),
    ("branches probe and paths", "on:\n  push:\n    branches: [probe]\n    paths: ['x']", False, False),
    # patterns the module does not model count as the real workflow they might be
    ("unmodelled class", "on:\n  push:\n    branches: ['m[a]in']", True, True),
    ("unmodelled question mark", "on:\n  push:\n    branches: ['mai?n']", True, True),
    ("unmodelled plus", "on:\n  push:\n    branches: ['ma+in']", True, True),
    ("unmodelled in branches-ignore", "on:\n  push:\n    branches-ignore: ['m[a]in']", True, True),
    ("unmodelled negation keeps the match", "on:\n  push:\n    branches: ['**', '!m[a]in']", True, True),
]


@pytest.mark.parametrize(("on_text", "can_run", "every_push"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_a_workflow_is_classified_as_github_would_run_it(on_text, can_run, every_push):
    assert triggers.classify(doc(on_text)) == (can_run, every_push)


def test_the_key_on_is_read_whether_pyyaml_gives_it_as_a_string_or_as_true():
    parsed = yaml.safe_load("on: push\n" + BODY)
    assert True in parsed and "on" not in parsed
    assert triggers.classify(parsed) == (True, True)
    quoted = yaml.safe_load("'on': push\n" + BODY)
    assert "on" in quoted and triggers.classify(quoted) == (True, True)


@pytest.mark.parametrize("text", ["name: x\n" + BODY, "name: x\non:\n" + BODY, "name: x\non: 5\n" + BODY])
def test_a_workflow_with_no_usable_on_cannot_run_on_a_push(text):
    assert triggers.classify(yaml.safe_load(text)) == (False, False)


def test_a_workflow_with_no_name_is_identified_by_its_path():
    unnamed = doc("on: push", name=None)
    assert triggers.workflow_name(unnamed, ".github/workflows/x.yml") == ".github/workflows/x.yml"
    assert triggers.workflow_name(doc("on: push"), "x.yml") == "probe"
    assert triggers.workflow_name({"name": ""}, "x.yml") == "x.yml"
    assert triggers.workflow_name({"name": 7}, "x.yml") == "x.yml"


def test_the_same_text_with_only_the_branch_filter_changed_flips_the_outcome():
    probe = "on:\n  push:\n    branches: [oidc-probe-156]"
    assert triggers.classify(doc(probe)) == (False, False)
    assert triggers.classify(doc(probe.replace("[oidc-probe-156]", "[oidc-probe-156, main]"))) == (True, True)
    assert triggers.classify(doc(probe.replace("branches: [oidc-probe-156]", "branches-ignore: [oidc-probe-156]"))) == (True, True)


# --- the check between the files, the trigger list and the gate's list ---------------------------

REAL = {
    "verify.yml": doc("on:\n  push:\n  pull_request:", "socboard verify"),
    "postgres.yml": doc("on:\n  push:\n  pull_request:", "socboard postgres"),
    "terraform-check.yml": doc("on:\n  push:\n    branches: [main]\n    paths: ['terraform/**']\n  pull_request:\n    paths: ['terraform/**']", "terraform check"),
    "publish.yml": doc("on:\n  workflow_run:\n    workflows: [socboard verify]\n    types: [completed]", "socboard publish"),
}
LISTED = {"socboard verify", "socboard postgres", "terraform check"}
REQUIRED = {"socboard verify", "socboard postgres"}


def check(extra=None, listed=LISTED, required=REQUIRED):
    workflows = {**REAL, **(extra or {})}
    return triggers.problems(workflows, set(listed), set(required), "publish.yml")


def test_the_baseline_is_clean():
    assert check() == []


@pytest.mark.parametrize(
    "on_text",
    [
        "on: push",
        "on: [push]",
        "on: [push, pull_request]",
        "on:\n  push:",
        "on:\n  push:\n    branches: ['**']",
        "on:\n  push:\n    branches: [main, 'release/*']",
        "on:\n  push:\n    branches-ignore: ['feature/*']",
        "on:\n  push:\n    branches: ['m[a]in']",
        "on:\n  push:\n    paths: ['x/**']",
    ],
)
def test_a_workflow_that_can_run_on_main_and_is_not_listed_is_refused_by_name(on_text):
    found = check({"new.yml": doc(on_text, "a new check")})
    assert any("a new check" in line and "not in publish.yml's trigger list" in line for line in found), found


@pytest.mark.parametrize(
    "on_text",
    [
        "on: pull_request",
        "on: [pull_request]",
        "on:\n  pull_request:\n    paths: ['x']",
        "on: workflow_dispatch",
        "on:\n  schedule:\n    - cron: '0 0 * * *'",
        "on:\n  push:\n    branches: [oidc-probe-156]",
        "on:\n  push:\n    branches-ignore: [main]",
        "on:\n  push:\n    tags: ['v*']",
    ],
)
def test_a_workflow_that_cannot_run_on_main_needs_no_entry(on_text):
    assert check({"probe.yml": doc(on_text, "a probe")}) == []


def test_an_unfiltered_push_probe_is_refused_and_the_message_says_what_to_do():
    found = check({"probe.yml": doc("on: push", "a probe")})
    assert found and "restrict `on.push.branches`" in found[0] and "a probe" in found[0]


def test_a_listed_workflow_that_cannot_run_on_main_is_refused():
    found = check({"probe.yml": doc("on: pull_request", "a probe")}, listed=LISTED | {"a probe"})
    assert any("cannot run on a push to main" in line and "a probe" in line for line in found)


def test_a_path_filtered_workflow_must_be_listed_but_must_not_be_required():
    assert check(required=REQUIRED | {"terraform check"})
    assert check(listed=LISTED - {"terraform check"})


def test_an_every_push_workflow_missing_from_the_required_list_is_refused():
    found = check({"new.yml": doc("on: push", "a new check")}, listed=LISTED | {"a new check"})
    assert any("required list" in line and "a new check" in line for line in found)


def test_a_required_name_with_no_workflow_is_refused():
    assert any("no such workflow" in line for line in check(required=REQUIRED | {"socboard image"}))


def test_a_workflow_with_no_name_is_listed_and_found_by_its_path():
    unnamed = doc("on: push", name=None)
    assert check({".github/workflows/x.yml": unnamed})
    found = check({".github/workflows/x.yml": unnamed}, listed=LISTED | {".github/workflows/x.yml"}, required=REQUIRED | {".github/workflows/x.yml"})
    assert found == []
