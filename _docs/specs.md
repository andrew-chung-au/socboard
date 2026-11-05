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

Authentication is minimal: the system is seeded with one Analyst and one Lead account (hashed passwords, bearer token). Identity matters because single-ownership, reassignment, and the audit trail ("who decided what") are only meaningful when the system knows who is acting.

## 3. Primary journeys

1. **Ingest → Queue.** An alert arrives via the API, is scored by fixed rules, and appears in the shared queue within the target latency, sorted by severity then age.
2. **Claim → Investigate → Close.** An analyst claims an open alert, adds notes, and moves it `New → Investigating → Closed` with a required closing reason. Every step is recorded in the audit trail.

A Lead variant of journey 2: a Lead reassigns a claimed case to another analyst, and the reassignment is recorded.

## 4. Requirements (user stories)

Each story is a tracker issue; acceptance criteria are written on the issue. Priority uses MoSCoW; the Must-haves are the Core release (Section 6).

**Ingest & scoring**
- (Must) As a source system, I POST an alert (source, type, affected asset, timestamp) so it enters the system.
- (Must) As the system, I assign severity from a fixed, explicit rule table (alert type × asset criticality) so severity is consistent and explainable.
- (Must) As an analyst, alert text is shown escaped and never executed, because alert content is untrusted.
- (Should) As a demoer, I trigger a synthetic alert generator so the board shows live activity.

**Queue**
- (Must) As an analyst, I see open alerts sorted by severity then age so the most urgent surface first.
- (Must) As an analyst, the queue reflects a new alert within the target latency (polling is acceptable).

**Triage workflow**
- (Must) As an analyst, I claim an open alert so I own it; an alert has at most one owner at a time.
- (Must) As an analyst, I add notes to a case I am working.
- (Must) As an analyst, I move a case `New → Investigating → Closed`; closing requires a reason (true positive, false positive, or duplicate).
- (Must) As the system, I reject blocked transitions (e.g. closing a `New` alert) with a clear error — never silently allowed.
- (Should) As a Lead, I reassign a claimed case to another analyst.

**Audit**
- (Must) As a Lead, I read a case's audit trail in order, start to finish: every status change, assignment, and note records who, what, and when.

**Cross-cutting**
- (Should) As an operator, one key journey emits metrics, traces, and structured logs sharing a trace id, with no synthetic PII or credentials in telemetry.

## 5. Expected behavior & invariants

These are the product invariants; agents must keep them (they also appear in `AGENTS.md`):

- A new alert appears in the queue within the target latency with a severity. *(Target: set and record the number N when the queue is built; default 5s.)*
- An alert has **at most one owner** at a time.
- Closing a case **requires a reason**.
- The audit trail for a case can be read **in order**, start to finish.
- Alert text is **untrusted**: escaped when shown, never treated as instructions.
- **No machine learning in the critical path.** Every severity and every status change is explainable and testable.

## 6. Scope

**Core (this release)** — the Must-have stories above: API ingest, rule-based scoring, seeded minimal auth, the sorted queue with polling, the claim/investigate/close workflow with blocked-transition errors, notes, and the audit trail. This is the end-to-end full-stack application that satisfies the project criteria.

**Target Enhancements** — scoped follow-on work that adds depth once Core is solid (see roadmap for ordering):
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
