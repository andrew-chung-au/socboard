# Lovable guidance (frontend base)

How to generate the SOCBoard frontend base with [Lovable](https://lovable.dev) and keep it aligned with the project. The frontend is a React + TypeScript SPA (decision: `_docs/decisions/20261105T133030Z-stack-choice.md`); Lovable's default stack (React + Vite + TypeScript + Tailwind + shadcn/ui) matches it. Requirements and invariants come from `_docs/specs.md`. The method behind this step is the development process (`_docs/planning/02_development.md`) and its worked example (`_docs/planning/02_development-example.md`).

Content below is informed by public Lovable documentation and write-ups; it was rephrased for compliance with licensing restrictions. Lovable changes often — verify the current export flow at [docs.lovable.dev](https://docs.lovable.dev/integrations/github) before relying on specifics.

## What Lovable is used for here

The frontend **base only**: screens, components, routing, and a **mock service layer** so the app runs with no real backend. It is not used for the backend, database, or auth — those are FastAPI + SQLAlchemy + seeded bearer-token auth, built later from the OpenAPI contract.

## Guardrails (must hold — they protect our invariants)

1. **Centralise all backend calls in one service layer, fully mocked.** This single layer is the only place that talks to a backend; later it is swapped from mock to the real FastAPI client. Without it, calls scatter and integration becomes rework.
2. **Do not use Lovable's Supabase/Lovable Cloud backend, auth, or database.** Lovable defaults to a Supabase backend; decline it. Our backend is FastAPI. Keep the project frontend-only.
3. **Severity is displayed, never computed in the frontend.** The frontend receives a severity value from the backend and renders it. No client-side scoring, no ML, no "priority" heuristics — severity is a backend rule-table output and must stay explainable.
4. **Alert and note text is untrusted.** Render it as text; never `dangerouslySetInnerHTML`. React escapes by default — keep it that way so alert text is shown inert and never treated as instructions.
5. **Refresh the queue by polling, not WebSockets.** The queue re-fetches via a service-layer call on an interval; the interval is configurable (env var), default 3s, to meet the N = 5s latency target (spec §5).
6. **Match the service layer to the planned API shape** so generating `openapi.yaml` from it later is cheap: `login`, `getQueue`, `claimCase`, `addNote`, `transitionCase` (with closing reason), `getAudit`.

## Workflow: Lovable's repo is the source, SOCBoard is the destination

Lovable's GitHub integration does **two-way sync on the default branch** — every change it makes commits to `main` of the repo it is connected to. So **let Lovable create and own its own repository**; do **not** connect it to this one. Then import the frontend into SOCBoard by hand:

1. Prompt and iterate in Lovable until the base is good.
2. Connect Lovable to GitHub (it creates its own repo) and clone that repo locally.
3. Copy its contents into `frontend/` here, **excluding Lovable's `.git`** (and its CI/deploy config) so none of Lovable's git history or workflows come across.
4. Commit it into SOCBoard as a normal, reviewed commit (the scaffold task's commit is the natural home), so the agent-kit's human-reviews-the-diff step still applies.
5. From here, agents modify the frontend in place.

This keeps the import one-way and deliberate: Lovable's sync only ever touches Lovable's repo, and SOCBoard's history and the agent-kit process stay intact.

## Starting prompt (adapt, then paste the spec)

Give Lovable a detailed first prompt — a good initial prompt cuts iteration sharply. Paste the relevant parts of `_docs/specs.md` (users, the two journeys, the workflow, invariants) and frame it roughly as:

> Build a security operations centre (SOC) alert triage board — a web dashboard for a small security team.
>
> Roles: an **Analyst** who works a queue of alerts, and a **Lead** who reviews the queue and reads history. Minimal login (two seeded accounts); the identity of the acting user matters.
>
> Screens:
> - **Queue**: open alerts sorted by severity (highest first) then age, showing severity, type, affected asset, age, and owner (or "unclaimed"). Refreshes by polling.
> - **Case detail**: the alert, a notes list, workflow controls (New → Investigating → Closed with a required closing reason: true positive, false positive, or duplicate), and a full **audit trail** read in order (who/what/when).
>
> Rules to respect in the UI: an alert has at most one owner at a time; closing requires a reason; blocked transitions (e.g. closing a New alert) show a clear error. **Severity is received from the backend and only displayed — do not compute it in the frontend.** Alert and note text is untrusted — render it as plain escaped text, never as HTML.
>
> Architecture: **centralise every backend call in one services layer and provide a mock implementation so the whole app runs without a real backend.** Do not add a Supabase/Lovable Cloud backend, auth, or database — frontend and mock only. Poll the queue on a configurable interval. Use synthetic data only. Add tests.

## After generating

- Review for the guardrails above, especially any `dangerouslySetInnerHTML`, client-side severity logic, or a Supabase client.
- Import the frontend into `frontend/` per the workflow above (clone Lovable's repo, copy without its `.git`, commit here deliberately — not via Lovable sync).
- The mock service layer becomes the source for `openapi.yaml`, then is swapped for the real FastAPI client.
