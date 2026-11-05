# Spec readiness gate

Project-owned. The planning-side gate the kit's Backlog step lacks: the minimum `_docs/specs.md` must contain before the PM builds a backlog from it, and what the PM does when it falls short. This is the proactive analogue of the reactive "Spec notes" path in `_docs/agent-kit/team/pm.md`.

Read this before building or reviewing the backlog (`_docs/agent-kit/process.md#backlog`). It does not change spec ownership: the spec is human-owned, agents propose and the human approves (`process.md#planning`). Full planning method: `_docs/planning/01_planning.md`.

## Definition of ready

The spec is ready to build a backlog from only when all of these are true. Check by reading `_docs/specs.md`; each line is answerable yes/no.

- [ ] **Problem + core assumption** — states the problem and the single assumption the project most needs to prove.
- [ ] **Users** — names each user role and what they do.
- [ ] **At least one end-to-end journey** — a happy path from trigger to outcome, concrete enough to derive tests from.
- [ ] **Requirements as checkable stories** — each requirement is phrased so acceptance criteria can be written against it (a reviewer could say yes/no by looking at a UI, an API response, or a test).
- [ ] **Explicit scope boundary** — what is in this release is separated from what is deferred.
- [ ] **Explicit non-goals** — a stated "won't have (this release)" list, not an empty section.
- [ ] **Invariants** — the product rules that must always hold (e.g. single owner, required closing reason, no ML in the critical path) are listed.

## When the spec is not ready

The PM does not build an implementation backlog on a spec that fails the gate. Instead, before proposing any implementation issue:

1. Name each failed item from the list above.
2. Propose the spec change that would satisfy it, as a spec-change proposal to the human (spec is human-owned; the human approves before it is written).
3. Only after the spec passes the gate does the PM propose implementation issues.

A single genuinely-not-applicable item is allowed if the PM states, in the backlog proposal, why it does not apply (for example a tool with no external interface has no "limit to refuse"). Silence is not an exemption.

## Enforcement

Non-scripted, the same way the kit enforces its other planning rules: the PM reads this file before building or reviewing the backlog (AGENTS.md "Read when" trigger and Project gotchas), confirms the gate in its backlog-proposal comment, and the human sees that confirmation before approving. There is no hook; the orchestrator's and human's reading is the gate.

A refused case: asked to build a backlog from a spec with no non-goals and no scope boundary, the PM must propose the spec changes first and must not create implementation issues.
