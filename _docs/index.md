# Docs index

No front matter; metadata lives here.

| Detail | Goes in | Not here |
|---|---|---|
| Session narrative and what was learned | a log entry | the README |
| A choice that may be revisited, with reasons and alternatives | a decision record | a record, a plan |
| Exact commands, numbers, full tables and caveats | a record | the README |
| One headline result and its limit, owned by the owner | the README, linking to the record | run tables |
| Proof for a decision (results, review, discussion) | where it was first recorded (issue, pull request or a record), linked from the decision | a copy in the decision record |
| Anything stable and long-lived | a canonical doc | a log entry |

Write once, link elsewhere: log entries link to stable decisions. The README holds one headline result and its limit, no run tables.

A copy is allowed only if the reader would otherwise have to follow a link before acting, and the copy names its source and has a check or a named sync step. Otherwise, link.

Word limits are defaults a project may change: README 300 words; this index 200 words plus one row per entry type. Project override: the README limit is 600 words, because it carries the product description; the index limit is unchanged.

## Entry types

By folder: "no doc without an index entry" can't work for hundreds of entries. This table is the only place that defines the folders; every other doc says "the folder the index names". Names: `procedures/session-summary.md`.

| Type | Folder | Template | Filename pattern | Not here |
|---|---|---|---|---|
| Plan: what we intend to do and in what order (roadmap files, requirements-like files) | `_docs/planning/` | `templates/roadmap.md` for roadmaps | `roadmap-<n>-<slug>.md`, or `<slug>.md` | measurements, decisions |
| Decision | `_docs/decisions/` | `templates/adr.md` | `<timestamp>-<slug>.md` | plans, measurements |
| Record: what was measured, found or read, with method and caveats (flat; the filename kind, such as `measure` or `source`, tells them apart) | `_docs/records/` | `templates/measurement.md`, `templates/source.md` | `<timestamp>-<kind>-<slug>.md` | decisions, plans |
| Runbook (added when the first repeatable operation appears) | `_docs/runbooks/` | `templates/runbook.md` | `<slug>.md` | one-off fixes, decisions |
| Session summary | `_session-summaries/` | `templates/session-summary.md` | see `procedures/session-summary.md` | decisions, plans |
| Human task | `_human-tasks/` | `templates/human-task.md` | `<timestamp>-human-task-<id>-<role>-<slug>.md` | decisions, records |

## Canonical docs

By file. Add project manifests here.

| Path | Canonical for | Status |
|---|---|---|
| `README.md` | What SOCBoard is and how to run it | Current |
| `AGENTS.md` | Instructions agents read first | Current |
| `_docs/specs.md` | The product specification (problem, users, journeys, requirements, scope, invariants) | Current |
| `_docs/planning/lovable-guidance.md` | How to generate and import the frontend base with Lovable | Current |
| `_docs/vendor/ponytail/` | Verbatim copy of upstream `ponytail-review` at the pinned commit, with its licence; loaded by path, never auto-discovered (source: `_docs/records/2026-11-06T0024Z-source-ponytail.md`) | Current |
| `_docs/team/reviewer.md` | The read-only Reviewer role for the ponytail trial (project-owned, outside the kit; pointers in `.claude/agents/`, `.opencode/agents/` and `.kiro/agents/`; decisions: `_docs/decisions/2026-11-06T0024Z-ponytail-trial.md`, `_docs/decisions/2026-11-10T0822Z-reviewer-role.md`) | Current |
| `_docs/records/2026-11-10T0946Z-source-lovable-frontend.md` | Where the `frontend/` base came from, its template, quirks and licence status (source record) | Current |
| `_docs/records/2026-11-10T1506Z-measure-ponytail-epoch-1-start.md` | Start of ponytail epoch 1: the commit, kit version, role-file versions, skill pin and excluded issues | Current |
| `_docs/records/2026-11-10T1518Z-measure-pm-sweep.md` | First PM sweep: figures at f41d503, seven proposals and the human's ruling | Current |
| `_docs/decisions/2026-11-11T0327Z-frontend-tests-in-make-test.md` | Why `make install` installs the frontend packages and `make test` and `make verify` run the frontend tests (issue #46) | Current |
| `_docs/decisions/2026-11-11T1511Z-openapi-contract.md` | Why `openapi.yaml` is hand-written first and is the source of truth for the HTTP API; the refusal order and the error body (issue #9) | Current |
| `_docs/runbooks/kiro-web-to-dev-container-handover.md` | What can and cannot run in Kiro Web, CI and the dev container; the per-issue hand-over map, the hand-over comment layout, and the fallback for when the dev container is not available (Engineer and QA in Kiro Web with CI as the runner; worked example #8) | Current |
| `pyproject.toml` | Python project metadata and dependencies | Current |
| `uv.lock` | Resolved dependency versions | Current |
| `Makefile` | Project commands | Current |
| `_docs/agent-kit/README.md` | The agent kit's guide | Current |
