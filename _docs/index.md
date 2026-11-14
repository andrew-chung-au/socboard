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
| `_docs/planning/deploy-backlog-proposal.md` | Draft Deploy backlog (D1 to D23) for the human's approval: review of roadmap 3, issue drafts, the effect of #22, open questions and spec notes; no issue filed yet | Proposed |
| `_docs/planning/deploy-coordination.md` | How Session A and Session B work the Deploy issues at the same time: roles, lanes and file ownership, claims, branches, shared files, merge order, the independent second QA, and message hygiene | Proposed |
| `_docs/planning/deploy-draft/reviewer-brief.md` | Brief for an outside reviewer of the Deploy plan: what to read, what to judge, known gaps, reply format | Proposed |
| `_docs/planning/deploy-draft/wave-2-issue-drafts.md` | Full draft bodies for Deploy wave 2 (D6a, D6b, D8, D9, D10, D15, D16, D17a to D17c, D18a, D18b, D21, D24); not filed | Proposed |
| `_docs/planning/deploy-draft/spec-notes-a-b.patch` | The exact diff of spec notes A and B for `_docs/specs.md` (protected; the human commits it) | Proposed |
| `_docs/vendor/ponytail/` | Verbatim copies of upstream `ponytail` (the Engineer's skill, `ponytail.md`) and `ponytail-review` at the pinned commit, with the licence; loaded by path, never auto-discovered (source: `_docs/records/2026-11-06T0024Z-source-ponytail.md`) | Current |
| `_docs/team/reviewer.md` | The read-only Reviewer role (no longer launched since 2026-11-14; kept for a one-off review the human asks for) (project-owned, outside the kit; pointers in `.claude/agents/`, `.opencode/agents/` and `.kiro/agents/`; decisions: `_docs/decisions/2026-11-14T0953Z-drop-reviewer.md`, `_docs/decisions/2026-11-12T2300Z-ponytail-adopt.md`, `_docs/decisions/2026-11-10T0822Z-reviewer-role.md`) | Superseded |
| `_docs/decisions/2026-11-12T2300Z-ponytail-adopt.md` | Why ponytail is used in normal work (the Engineer's skill and the Reviewer; the Reviewer part is superseded by `_docs/decisions/2026-11-14T0953Z-drop-reviewer.md`), how its benefit is measured for review (before and after, from `make ponytail-metrics` and the human's finding labels), and why the controlled trial was shelved | Current |
| `_docs/decisions/2026-11-14T0953Z-drop-reviewer.md` | Why the Reviewer is dropped and QA stays the only review step; the Engineer keeps the ponytail skill | Current |
| `_docs/records/2026-11-10T0946Z-source-lovable-frontend.md` | Where the `frontend/` base came from, its template, quirks and licence status (source record) | Current |
| `_docs/records/2026-11-10T1506Z-measure-ponytail-epoch-1-start.md` | Start of ponytail epoch 1: the commit, kit version, role-file versions, skill pin and excluded issues (trial shelved on 2026-11-12, can be resumed: `_docs/planning/roadmap-2-ponytail-trial.md`) | Shelved |
| `_docs/records/2026-11-10T1518Z-measure-pm-sweep.md` | First PM sweep: figures at f41d503, seven proposals and the human's ruling | Current |
| `_docs/records/2026-11-13T0424Z-measure-pm-sweep.md` | Fifth PM sweep: figures at 3dc8b90, fourteen proposals and the human's ruling | Current |
| `_docs/records/2026-11-13T1008Z-measure-pm-sweep.md` | Sixth PM sweep: figures at a63ef30, thirteen proposals (the local ones applied as steering or roadmap edits; the kit edits wait for the human's exact-diff approval), the answers to the human's questions and the human's rulings | Current |
| `_docs/records/2026-11-13T0424Z-measure-ponytail-review.md` | Ponytail before-and-after review: #8 to #12 against #102, #99, #14, #15, #16 and #18, raw figures and the human's finding labels; directional only | Current |
| `_docs/decisions/2026-11-11T0327Z-frontend-tests-in-make-test.md` | Why `make install` installs the frontend packages and `make test` and `make verify` run the frontend tests (issue #46) | Current |
| `_docs/decisions/2026-11-11T1511Z-openapi-contract.md` | Why `openapi.yaml` is hand-written first and is the source of truth for the HTTP API; the refusal order and the error body (issue #9) | Current |
| `_docs/decisions/2026-11-12T0834Z-data-model.md` | Why SQLAlchemy 2.x models and Alembic migrations, integer keys shown as decimal strings, CHECK-constrained strings, database triggers for an append-only audit trail (with the application guard as a second layer) and one conditional UPDATE for single ownership (issue #11) | Current |
| `_docs/decisions/2026-11-12T2255Z-auth-tokens.md` | Why signed stateless bearer tokens (HMAC-SHA256, 8-hour lifetime) and scrypt password hashes from the standard library, with the role read from the database on each request; the scrypt parameters as built (issue #13) | Current |
| `_docs/decisions/2026-11-13T0158Z-ingest-api-key.md` | Why one shared ingest API key in the `X-API-Key` header, read from `SOCBOARD_INGEST_API_KEY`, compared as SHA-256 digests, with ingest closed when the key is unset, empty or under 16 characters; user tokens and the key stay separate (issue #14) | Current |
| `_docs/decisions/2026-11-13T0537Z-close-floor-reflection.md` | Why `issue-close.sh` ignores an Engineer comment that is only a reflection when it sets the close floor, and still counts work comments, with or without a reflection; a local deviation from the kit with a note for upstream (issue #63) | Current |
| `_docs/decisions/2026-11-13T2156Z-deploy.md` | The Deploy phase rules: the seven decisions of 2026-11-12, Postgres audit triggers, in-process limits, the amendment moving IAM and budgets to the human-applied part, region `us-east-1`, accepted risks, Postgres 16, and what state, GitHub secrets and the image must never hold (issue #139) | Current |
| `_docs/decisions/2026-11-13T2156Z-serve-built-frontend.md` | Why the app serves the built frontend with one fallback route registered last that never matches `/api`, a path-containment check, extension-less paths falling back to `index.html`, and one setting, `SOCBOARD_STATIC_DIR` (issue #140) | Proposed |
| `_docs/decisions/2026-11-13T2156Z-container-bind-flag.md` | Why the container image opts in to a non-loopback bind with the `--allow-non-loopback-host` flag of `python -m socboard.server`, not an environment variable, so `make backend-run` and the probe stay loopback-only (issue #142) | Proposed |
| `_docs/decisions/2026-11-14T0420Z-agent-push-claude-branches.md` | Why a project-owned hook (`.claude/hooks/guard_git_local.py`) wrapping the kit's git guard lets an agent push only its own `claude/` work branch to `origin`, so a cloud session's work can be reviewed; every other push stays the human's; a local deviation from the kit (issue #22 session) | Current |
| `_docs/decisions/2026-11-14T1120Z-postgres-driver.md` | Why psycopg 3 is the Postgres driver, why migration `0001` guards its SQLite-only parts instead of a second history, why Postgres runs in its own CI job, and why a failed Postgres connection logs fixed text (issue #143) | Proposed |
| `_docs/decisions/2026-11-14T0907Z-instance-iam-snapshots-budgets.md` | Why the instance role (Systems Manager core policy plus one secret read), the daily snapshot schedule with its role (Data Lifecycle Manager, tag `project=socboard`, 7 kept) and the budgets (50, 80, 100 percent) are one Terraform part applied only by the human (issue #162) | Proposed |
| `openapi.yaml` | The HTTP API contract (OpenAPI 3.1): operations, schemas, refusal codes and the order of checks; checked by `make contract-check` (issue #9) | Current |
| `_docs/runbooks/kiro-web-to-dev-container-handover.md` | What can and cannot run in Kiro Web, CI and the dev container; the per-issue hand-over map, the hand-over comment layout, and the fallback for when the dev container is not available (Engineer and QA in Kiro Web with CI as the runner; worked example #8) | Current |
| `pyproject.toml` | Python project metadata and dependencies | Current |
| `uv.lock` | Resolved dependency versions | Current |
| `Makefile` | Project commands | Current |
| `_docs/agent-kit/README.md` | The agent kit's guide | Current |
