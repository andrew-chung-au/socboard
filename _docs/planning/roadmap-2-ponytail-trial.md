# Roadmap 2: Ponytail trial

Order and status only. Method and criteria: `_docs/planning/ponytail-trial-protocol.md`. Decision: `_docs/decisions/2026-11-06T0024Z-ponytail-trial.md`.

- **Status:** next (Core is now; the baseline runs on Core issues from #2 onward)
- **Goal:** Learn whether, and for which function, ponytail improves how this project's agents work, measured against our own baseline.
- **Tasks, in order** (file as issues when groomed; numbers added then):
  1. Human habit, starting now: record tool and model on every `Launch usage` line; add an attribution line to OpenCode commits.
  2. Human approves the decision record and protocol.
  3. Vendor the pinned `ponytail` and `ponytail-review` skills into `.claude/skills/` and `.kiro/skills/`; confirm each tool you use can load them.
  4. Metrics script and make target, computing every figure from git and GitHub only.
  5. Reviewer role: one role file, thin pointers in each tool, read-only; `make assert-clean` after each run.
  6. Phase 1: baseline plus shadow review on at least 5 qualifying issues; gate review at the sweep.
  7. Phases 2 and 3 as the protocol says.
- **Not in this phase:** installing any ponytail plugin; scope, debt-ledger and audit functions (each needs its own trial after the first two are decided); any AGENTS.md rule before Phase 2 starts.
- **Archive when:** the Phase 3 gate decision record exists.
