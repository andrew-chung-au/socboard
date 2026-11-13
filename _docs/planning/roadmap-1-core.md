# Roadmap 1: Core

Order and status only. Acceptance criteria live on the issues; open/closed is read from the tracker. Scope and reasons: `_docs/specs.md`. Stack choice (approved): `_docs/decisions/20261105T133030Z-stack-choice.md`.

- **Status:** now
- **Goal:** A full-stack SOCBoard that ingests alerts, scores them by fixed rules, shows a live-updating queue, runs the claim→investigate→close workflow, and records an audit trail — running locally with one command.
- **Tasks, in order** (tracker issues; open or closed is read from the tracker):
  1. #7 Review and record the imported Lovable frontend base.
  2. #27 Make the frontend build static and remove Lovable coupling.
  3. #8 Complete the frontend base: mock rule enforcement, hostile fixtures, frontend tests, `make` targets.
  4. #9 OpenAPI contract (`openapi.yaml`), derived from the service layer and the spec.
  5. #10 Backend skeleton: FastAPI app, health endpoint, error shape.
  6. #11 Data model and migrations, including the one audit-writing function.
  7. #12 Rule-based severity scoring and seeded asset inventory.
  8. #13 Seeded minimal auth: two Analysts and a Lead, bearer token.
  9. #14 Ingest API with an API key.
  10. #15 Queue and case-detail read API.
  11. #16 Claim and single-owner enforcement.
  12. #17 Workflow transitions with a required closing reason.
  13. #18 Notes on a case.
  14. #19 Audit trail read API.
  15. #20 Closed-cases list for Leads.
  16. #21 Connect the frontend to the real API and add the login screen.
  17. #22 Integration tests for both journeys and the one-command `make run`.
- **Pace (human ruling, 2026-11-12):** CI is the test loop; one pull request per issue; the next two or three Core issues may be groomed ahead and approved together; one small supporting issue may run alongside the main issue while it waits. Details: `.kiro/steering/delivery-pace.md`.
- **Supporting issues, in order** (tracker issues outside the task list; each lands at its trigger, the Core issue it goes before or the event that brings it up; open or closed is read from the tracker; none changes the Core order). Every new issue is placed in this list, with a trigger, when it is filed, so that it is groomed when its trigger comes and does not wait unseen (added after the 2026-11-12 prioritization review found twelve issues outside any order):
  1. #74 Add the health route to the OpenAPI contract. After #10 closes, before #13. (Done.)
  - #86 Add the ingest field limits to the OpenAPI contract. Before #14 (#14 depends on it).
  2. #63 Make `make issue-close` ignore reflection-only Engineer comments. Epoch batch, first; before Reflections are collected on every issue.
  3. #49 Close the weakened-test gaps for Vitest markers and test support files. Epoch batch, together with #65.
  4. #65 Make the CI weakened-test check compare a branch with its merge base. Epoch batch, together with #49.
  5. #62 Fix future-dated timer alerts and the order-dependent timer test. Before #21.
  6. #59 Make the dev server reachable from the browser in the dev container. Before #21.
  7. #54 Close the cheap gaps in the frontend guardrail scan. Before #21, after #62 and #59.
  8. #75 Add `make frontend-lint` and `make frontend-format`. Before #31.
  9. #31 Make `bun run lint` pass in `frontend/`. After #75 (its body gains "Depends on #75" when it is next groomed); before #21 if ready by then, otherwise after #22.
  10. #60 Protect the frontend dependency files. Any time, and before #21 at the latest.
  11. #73 Add `make issue-body`. Any time; before the next Heavy issue's second QA launch if it can.
  12. #71 Fix the `make contract-check` help and usage text. In the same sitting as #73 (both need the human's approval of a `Makefile` diff).
  13. #80 Harden `make backend-run` and `make backend-stop`. In the epoch batch (it changes what two existing commands do), so before #22.
  14. #93 Run the frontend lint in `make verify` and CI. After #75 and #31; it moves the epoch, so it joins the next epoch batch after the current one.
  15. #99 Harden the audit guard's remaining DDL and id gaps (#11's Low QA findings L1 to L5). After #11 closes, before #14 (the first issue that writes audit entries in use); proposed Heavy.
  - #102 A request body that cannot be parsed gets 500 instead of 422 (#13 follow-up). Before #14, which takes the first JSON body from outside.
  - #107 Simplify the audit guard (from #99's Reviewer and QA findings; proposed Heavy). Any time after #99 closes; before Deploy. At grooming, also add: keep the HTTP-layer module list in `tests/test_data_audit.py` in one place, because #13, #15 and later route issues each add a name (human ruling, #15).
  - #110 Simplify the ingest route (from #14's Reviewer and QA findings; proposed Standard). Any time after #14 closes; before Deploy. At grooming, also add #18's Reviewer findings 18-1, 18-4, 18-5 and 18-7 (human ruling, #18): the redundant rollback in `add_note`, the large 401 test grid, the duplicated note reply builder, and the "read-only" docstrings. Also add #15's Reviewer findings (human ruling, #15):
    - one shared `CaseSummary` builder for ingest and the queue (15-1);
    - the repeated contract and hostile-text tests (15-2, 15-3);
    - the single-use not-found message (15-4);
    - the triple 422 explanation (15-5);
    - "no queue paging", recorded as a known limit (15-6).
  - #103 A deleted user's id can be reused, so their old token is accepted as the new user (#13 follow-up, low). Before any issue that deletes users, and before Deploy.
  - #104 Small follow-ups from #13 (missing database, seed variables, seed help text, hash bounds, secret check; low). Any time before #22; needs the human's approval of a `Makefile` and `AGENTS.md` diff.
  16. #100 Pin CI's Python version to the project's 3.12 (CI ran 3.14 on #11). Any time, before #22; needs the human's approval of the workflow diff.
  - The epoch batch (#63, #49, #65, #80, and the approved kit edits waiting for it, including #42's item 3; drafts and rulings in `_docs/planning/epoch-batch-1/README.md`) lands once, after the fifth qualifying control issue closes. #9 counts as a control issue (human ruling, 2026-11-12), so the fifth is #11 (closed 2026-11-12). Sweep 4 added items 9 to 11 (`_docs/records/2026-11-12T2107Z-measure-pm-sweep.md`). **The ponytail trial was shelved on 2026-11-12 (human ruling, after #12 closed; it can be resumed: `_docs/planning/roadmap-2-ponytail-trial.md`; `_docs/decisions/2026-11-12T2300Z-ponytail-adopt.md`), so for now there are no epochs and no batch landing point. Work focuses on the Core task list.** #63, #49, #65 and #80 are ordinary supporting issues now, picked up when useful. The drafted patches in `_docs/planning/epoch-batch-1/` are ordinary process edits, each landing when the human approves its exact diff, with no need to land together. Reflection comments may carry commit hashes again.

- **Not in this phase:** synthetic alert generator and Lead reassignment (Should-haves). Deploy, then DevOps (observability, alerting, on-call agent) are the next two phases, outlined in `_docs/specs.md` §6 and given their own roadmaps when Core is done. Target Enhancements (LangGraph advisory triage, n8n, MCP/VulnTriage) — future roadmap. See `_docs/specs.md` §6–7.
- **Archive when:** every Core issue is closed and `make run` brings up the full stack.
