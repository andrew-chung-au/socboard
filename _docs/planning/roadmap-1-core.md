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
- **Supporting issues, in order** (tracker issues outside the task list; each lands at its trigger, the Core issue it goes before or the event that brings it up; open or closed is read from the tracker; none changes the Core order). Every new issue is placed in this list, with a trigger, when it is filed, so that it is groomed when its trigger comes and does not wait unseen (added after the 2026-11-12 prioritization review found twelve issues outside any order):
  1. #74 Add the health route to the OpenAPI contract. After #10 closes, before #13.
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
  - The epoch batch (#63, #49, #65, #80, and the approved kit edits waiting for it, including #42's item 3; drafts and rulings in `_docs/planning/epoch-batch-1/README.md`) lands once, after the fifth qualifying control issue closes. #9 counts as a control issue (human ruling, 2026-11-12), so the fifth is #11. Until then, Reflection comments carry no commit hashes. #63, #49, #65 and #80 move the ponytail epoch (`_docs/planning/ponytail-trial-protocol.md`, Amendment 2); the others do not.

- **Not in this phase:** synthetic alert generator and Lead reassignment (Should-haves). Deploy, then DevOps (observability, alerting, on-call agent) are the next two phases, outlined in `_docs/specs.md` §6 and given their own roadmaps when Core is done. Target Enhancements (LangGraph advisory triage, n8n, MCP/VulnTriage) — future roadmap. See `_docs/specs.md` §6–7.
- **Archive when:** every Core issue is closed and `make run` brings up the full stack.
