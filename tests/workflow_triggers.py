"""Which workflows GitHub runs on a push to `main` (#258), decided from a parsed workflow file.

GitHub's rules, from the workflow-syntax documentation ("on", "on.<push>.<branches|tags>",
"on.<push>.<paths>", "Filter pattern cheat sheet"): `VERIFY:` each against
https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions

- `on:` is a string (`on: push`), a list (`on: [push, pull_request]`) or a mapping; a mapping's value
  for an event may be null. PyYAML reads the key `on` as the boolean `True`.
- No `push` event: the workflow cannot run on a push.
- `push` with no `branches`, `branches-ignore` or `tags` filter: runs on every push to every branch.
- `push` with only `tags`: runs for a tag push, not a branch push.
- `branches`: runs on a push to `main` only if an entry matches `main`; entries are patterns where `*` is
  any characters except `/`, `**` is any characters, and an entry that starts with `!` removes what the
  entries before it matched (later entries win).
- `branches-ignore`: runs unless an entry matches `main`.
- `paths` or `paths-ignore`: the workflow runs only when a changed file matches, so it can run on a push
  to `main` but not on every one.

A pattern this module does not model (`?`, `+`, a character class) is treated as the real workflow it
might be: a positive entry matches, a `branches-ignore` entry and a `!` entry do not. A mistake therefore
errs towards "this workflow can run on main", never towards "it cannot".
"""

import re
from collections.abc import Mapping

BRANCH = "main"
MODELLED = re.compile(r"[A-Za-z0-9_./*-]+")
UNFILTERED_HINT = (
    "restrict `on.push.branches` to the probe's own throwaway branch, or list the workflow in publish.yml's `workflows:`"
)


def events(doc: Mapping) -> dict:
    """The workflow's `on:` as a mapping of event name to its settings (None when it has none)."""
    on = doc.get("on", doc.get(True))
    if on is None:
        return {}
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {str(event): None for event in on}
    if isinstance(on, Mapping):
        return {str(event): settings for event, settings in on.items()}
    return {}


def workflow_name(doc: Mapping, path: str) -> str:
    """What `workflow_run` matches: the `name`, or the file path when the workflow has none."""
    name = doc.get("name")
    return name if isinstance(name, str) and name else path


def _patterns(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _matches_main(pattern: str) -> bool | None:
    """True or False for a pattern this module models; None for one it does not."""
    if not MODELLED.fullmatch(pattern):
        return None
    regex = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(regex, BRANCH) is not None


def _branches_include_main(entries: list[str]) -> bool:
    matched = False
    for entry in entries:
        negated = entry.startswith("!")
        verdict = _matches_main(entry[1:] if negated else entry)
        if negated:
            if verdict is True:
                matched = False
        elif verdict is None or verdict:
            matched = True
    return matched


def classify(doc: Mapping) -> tuple[bool, bool]:
    """(can run on a push to main, runs on every push to main)."""
    settings = events(doc).get("push", "absent")
    if settings == "absent":
        return False, False
    if not isinstance(settings, Mapping):
        return True, True  # `push` alone, or `push:` with nothing under it
    branches = settings.get("branches")
    ignore = settings.get("branches-ignore")
    tags = settings.get("tags")
    if branches is not None:
        if not _branches_include_main(_patterns(branches)):
            return False, False
    elif ignore is not None:
        if any(_matches_main(entry) is True for entry in _patterns(ignore)):
            return False, False
    elif tags is not None:
        return False, False  # only tags are filtered: a branch push does not start it
    conditional = settings.get("paths") is not None or settings.get("paths-ignore") is not None
    return True, not conditional


def _sort(workflows: Mapping[str, Mapping], own: str) -> tuple[set[str], set[str], set[str]]:
    """(names, names that can run on a push to main, names that run on every push to main), without `own`."""
    names, can_run, every_push = set(), set(), set()
    for path, doc in workflows.items():
        if path == own:
            continue
        name = workflow_name(doc, path)
        names.add(name)
        runs, always = classify(doc)
        if runs:
            can_run.add(name)
        if always:
            every_push.add(name)
    return names, can_run, every_push


def list_problems(workflows: Mapping[str, Mapping], listed: set[str], own: str) -> list[str]:
    """What is wrong between the workflow files and publish.yml's trigger list (`own` is publish.yml's path)."""
    _, can_run, _ = _sort(workflows, own)
    found = []
    unlisted = sorted(can_run - listed)
    if unlisted:
        found.append(
            f"can run on a push to main but are not in publish.yml's trigger list: {unlisted}; {UNFILTERED_HINT}"
        )
    stale = sorted(listed - can_run)
    if stale:
        found.append(f"are in publish.yml's trigger list but cannot run on a push to main: {stale}")
    return found


def required_problems(workflows: Mapping[str, Mapping], required: set[str], own: str) -> list[str]:
    """What is wrong between the workflow files and the gate's required list."""
    names, _, every_push = _sort(workflows, own)
    found = []
    missing = sorted(required - names)
    if missing:
        found.append(f"are required by the gate but there is no such workflow: {missing}")
    if required != every_push:
        found.append(
            f"the gate's required list must equal the workflows that run on every push to main: "
            f"required only {sorted(required - every_push)}, every-push only {sorted(every_push - required)}"
        )
    return found


def problems(workflows: Mapping[str, Mapping], listed: set[str], required: set[str], own: str) -> list[str]:
    """Both checks: the trigger list and the required list against the workflow files."""
    return list_problems(workflows, listed, own) + required_problems(workflows, required, own)
