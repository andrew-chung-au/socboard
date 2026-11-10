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

- **Not in this phase:** synthetic alert generator and Lead reassignment (Should-haves). Deploy, then DevOps (observability, alerting, on-call agent) are the next two phases, outlined in `_docs/specs.md` §6 and given their own roadmaps when Core is done. Target Enhancements (LangGraph advisory triage, n8n, MCP/VulnTriage) — future roadmap. See `_docs/specs.md` §6–7.
- **Archive when:** every Core issue is closed and `make run` brings up the full stack.
