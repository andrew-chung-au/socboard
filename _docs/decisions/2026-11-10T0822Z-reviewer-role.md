# Decision: Add a project-owned, read-only Reviewer role outside the kit

One file per choice that may be revisited. **Status: approved.** The human approved issue #25, whose `## Decision record` section this file copies, by adding the `approved` label on 2026-11-10 (hash `587a86234b4a49aa75def7f364e21afc1e18856bf4eb12d4d9c0a2a6e98ccae0`). Heavy tier: it adds a role and changes how agents are launched. It carries out the role the approved trial decision already allows.

- **Decision:** Add one reviewer role as a project-owned file (`_docs/team/reviewer.md`) outside the kit, with thin per-tool pointers (`.claude/agents/reviewer.md`, `.opencode/agents/reviewer.md`). It is read-only by tool setting where the tool supports it, and by `make assert-clean` plus instruction where it does not.
- **Reason:** The kit makes the orchestrator the only launcher and has no reviewer; the trial needs independent review without touching kit files. Keeping the role outside `_docs/agent-kit/` keeps the kit pristine for upstream comparison.
- **Alternatives:**
  - *Put the role in `_docs/agent-kit/team/`:* drifts from the kit manifest and is protected.
  - *Let QA do the review:* QA is the control being compared.
  - *Have the Engineer self-review:* the protocol forbids a role scoring its own work.
- **Trade-offs:** In Kiro Web read-only rests on instruction and an after-the-fact check, not a permission block. A reviewer that wrote something would be caught only by `make assert-clean`, and the human's diff review is the final check. The Claude Code pointer cannot stop shell commands, so the role file's rule covers them.
- **Evidence:** Issue #25; `_docs/decisions/2026-11-06T0024Z-ponytail-trial.md`; `_docs/planning/ponytail-trial-protocol.md`.
