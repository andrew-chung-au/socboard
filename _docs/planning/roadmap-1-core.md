# Roadmap 1: Core

Order and status only. Acceptance criteria live on the issues; open/closed is read from the tracker. Scope and reasons: `_docs/specs.md`. Stack choice (approved): `_docs/decisions/20261105T133030Z-stack-choice.md`.

- **Status:** now
- **Goal:** A full-stack SOCBoard that ingests alerts, scores them by fixed rules, shows a live-updating queue, runs the claim→investigate→close workflow, and records an audit trail — running locally with one command.
- **Tasks, in order** (file as tracker issues; numbers filled in when created):
  1. Approve stack choice (decision record) and scaffold backend + frontend skeleton.
  2. Data model + migrations: alert, case/status, assignment, note, audit entry, users.
  3. Seeded minimal auth: Analyst and Lead accounts, bearer token.
  4. Ingest API: POST alert (source, type, asset, timestamp); escape untrusted text.
  5. Rule-based scoring: explicit type × asset-criticality severity table.
  6. Queue API + UI: open alerts sorted by severity then age; polling refresh within target latency.
  7. Claim + single-owner enforcement.
  8. Workflow transitions New→Investigating→Closed, required closing reason, blocked-transition errors.
  9. Notes on a case.
  10. Audit trail: ordered who/what/when read endpoint + view.
  11. Unit + integration tests runnable with one command; `make run` target.

- **Not in this phase:** synthetic alert generator and Lead reassignment (Should-haves). Deploy, then DevOps (observability, alerting, on-call agent) are the next two phases, outlined in `_docs/specs.md` §6 and given their own roadmaps when Core is done. Target Enhancements (LangGraph advisory triage, n8n, MCP/VulnTriage) — future roadmap. See `_docs/specs.md` §6–7.
- **Archive when:** every Core issue is closed and `make run` brings up the full stack.
