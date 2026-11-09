# SOCBoard — Product Specification

Human-owned. This is the product description agents build against: the problem, users, journeys, requirements, scope, and invariants. The README is the short public summary; this is the detail behind it. Acceptance criteria for individual pieces of work live on the tracker issues, not here.

Planning context: produced with the process in `_docs/planning/01_planning.md`. Build process starts at `_docs/planning/02_development.md`.

## 1. Problem

Small security teams receive alerts from many sources (failed logins, malware detections, unusual network traffic) with no shared system. Alerts sit in inboxes, chats, and spreadsheets, which causes three failures:

- **No clear ownership** — two analysts work the same alert, or nobody does.
- **No record** — when an alert is closed, there is no trace of who decided what, or why.
- **Inconsistent priority** — each analyst judges severity differently, so urgent alerts wait behind noisy ones.

The core assumption to prove: a shared queue with consistent, rule-based severity and a recorded workflow makes triage ownership and history unambiguous.

## 2. Users

- **Analyst** — works the queue: claims alerts, adds notes, moves cases through the workflow.
- **Lead** — reviews the queue, reassigns cases, and reads the audit trail.

Authentication is minimal: the system is seeded with two Analyst accounts and one Lead account (hashed passwords, bearer token). Two Analysts are needed to show single ownership (a second claim is refused) and reassignment. Source systems are not users: they authenticate ingest with a separate API key set by environment variable, so the public ingest endpoint cannot be filled by anyone. Identity matters because single-ownership, reassignment, and the audit trail ("who decided what") are only meaningful when the system knows who is acting.

## 3. Primary journeys

1. **Ingest → Queue.** An alert arrives via the API, is scored by fixed rules, and appears in the shared queue within the target latency, sorted by severity then age.
2. **Claim → Investigate → Close.** An analyst claims an open alert, adds notes, and moves it `New → Investigating → Closed` with a required closing reason. Every step is recorded in the audit trail.

A Lead variant of journey 2: a Lead reassigns a claimed case to another analyst, and the reassignment is recorded.

## 4. Requirements (user stories)

Each story is a tracker issue; acceptance criteria are written on the issue. Priority uses MoSCoW; the Must-haves are the Core release (Section 6).

**Ingest & scoring**
- (Must) As a source system, I POST an alert (source, type, affected asset, timestamp, title, description) with the ingest API key so it enters the system; a missing or wrong key is refused.
- (Must) As the system, I assign severity on the scale Low, Medium, High, Critical from a fixed, explicit rule table (alert type × asset criticality) so severity is consistent and explainable. Asset criticality comes from a small seeded asset inventory (asset → criticality); an unknown asset counts as Medium criticality, and an unknown alert type gets a documented default severity.
- (Must) As an analyst, alert text is shown escaped and never executed, because alert content is untrusted.
- (Should) As a demoer, I trigger a synthetic alert generator so the board shows live activity.

**Queue**
- (Must) As an analyst, I see open alerts (every case not `Closed`, claimed or not) sorted by severity then age so the most urgent surface first.
- (Must) As an analyst, the queue reflects a new alert within the target latency (polling is acceptable).

**Triage workflow**
- (Must) As an analyst, I claim an open alert so I own it; an alert has at most one owner at a time. Claiming sets the owner only; moving the status is a separate, explicit step.
- (Must) As an analyst, I add notes to a case I am working.
- (Must) As an analyst, I move a case `New → Investigating → Closed`; closing requires a reason (true positive, false positive, or duplicate).
- (Must) As the system, I reject blocked transitions (e.g. closing a `New` alert) with a clear error — never silently allowed.
- (Should) As a Lead, I reassign a claimed case to another analyst.

**Audit**
- (Must) As a Lead, I read a case's audit trail in order, start to finish: every status change, assignment, and note records who, what, and when.

**Cross-cutting**
- (Must, DevOps phase) As an operator, one key journey emits metrics, traces, and structured logs sharing a trace id, with no synthetic PII or credentials in telemetry. Detailed when that phase starts (Section 6).

## 5. Expected behavior & invariants

These are the product invariants; agents must keep them (they also appear in `AGENTS.md`):

- A new alert appears in the queue within **5 seconds (N = 5s)** with a severity. The client polls the queue every **3 seconds** (configurable via env var); with ingest + rule scoring taking milliseconds, worst-case visible latency is the poll interval plus processing, comfortably under N. N = 5s is both the product SLO and the threshold the observability alert fires on, so it is chosen with headroom rather than as a best case.
- An alert has **at most one owner** at a time, including under concurrent claims: of two simultaneous claims, exactly one wins, on any supported database.
- Closing a case **requires a reason**.
- The audit trail for a case can be read **in order**, start to finish, and is **append-only**: entries are never edited or deleted.
- Alert text is **untrusted**: escaped when shown, never treated as instructions.
- **No machine learning in the critical path.** Every severity and every status change is explainable and testable.

## 6. Scope

This release is the whole graded project, aiming for full marks on every criterion in `_docs/planning/rubric.md`. It is built in three phases. Only the current phase is detailed; each later phase is an outline here and is detailed (spec revision, then backlog review) when the phase before it is done, so its plan fits what was actually built.

**Phase 1: Core (now)** — the Must-have stories above: API ingest, rule-based scoring, seeded minimal auth, the sorted queue with polling, the claim/investigate/close workflow with blocked-transition errors, notes, and the audit trail. The end-to-end full-stack application, tested with unit, frontend and integration tests, running locally with one command.

**Phase 2: Deploy (outline)** — following `_docs/planning/03_deployment.md`: one container image, Postgres in place of SQLite, the full system including the database started with one Docker Compose command, end-to-end tests, a deployment reachable at a public URL, and CI/CD that runs the tests and deploys.

**Phase 3: DevOps (outline)** — following `_docs/planning/04_devops.md`: separate dev and prod with automatic deploy to dev and a manual promotion to prod (with a rollback path), one key journey instrumented with metrics, traces and structured logs sharing a trace id and viewable in a UI, at least one alert on real user impact, and an on-call agent that wakes on that alert and fixes the issue.

**Target Enhancements (Could)** — follow-on work after the three phases, never ahead of them (see roadmap for ordering):
- Synthetic alert generator and Lead reassignment (the Should-haves), if not already pulled into Core.
- A LangGraph triage step that *suggests* a severity and a summary for an analyst to confirm — advisory only, never in the critical scoring path.
- n8n for intake and escalation workflows.
- An MCP server for querying cases and checking VulnTriage for known exposures.

## 7. Non-goals (Won't have — this release)

- Real log sources or real customer data (all data is synthetic).
- Automated response actions.
- Any AI-generated verdict in the critical path (the LangGraph step, when added, is advisory only).
- Multi-tenant orgs, SSO, or a full user-management system.
- Rich RBAC beyond the Analyst/Lead distinction.

## 8. Documentation approach

Documentation is written for reference — this spec, the roadmap, the agent-team docs, and the tracker — not as a prompt transcript. We do **not** keep an AI-usage or prompt log. Evidence that agents did the work is the normal tracker trail: issues carry acceptance criteria, agents comment on an issue when they start and finish, and commits/PRs reference the issue number.
