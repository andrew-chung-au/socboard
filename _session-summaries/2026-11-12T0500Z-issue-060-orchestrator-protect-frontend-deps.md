# Issue #60: Protect the frontend dependency files

**Date:** 2026-11-12T05:00Z (UTC)
**Spec version:** d913e19

## What changed and why

Tier: Standard (from the PM proposal; no tier label). A supporting issue run alongside #11 while #11 waits for outside answers (delivery-pace ruling, 2026-11-12). Ponytail trial: not a control issue. Epoch: the human's call (see Decisions); the orchestrator recommends no move.

- `agent-kit.conf`: `frontend/package.json`, `frontend/bun.lock` and `frontend/bunfig.toml` added to `PROTECTED`, so a commit that changes them is refused without `HUMAN_APPROVED=1`. Committed by the orchestrator with `HUMAN_APPROVED=1` after the human approved the exact diff in this session.
- First issue under the delivery-pace rules: CI was the test loop (run 43161409665: `bun install --frozen-lockfile`, 121 Python and 154 frontend tests, VERIFY: PASS), and the refused and allowed cases were proved in throwaway worktrees under `.scratch/` by running `.githooks/pre-commit` directly (hooks are not installed in Kiro Web). No human hand-over.
- One PR for the issue: this summary is on the issue branch.
- Alongside, in the same session: #12, #13 and #14 checked ahead and re-groomed with the human's rulings (Confirms and PM re-records posted); #86 filed and groomed (the ingest limits in the contract, before #14); the spec's Phase 2 outline gains login attempt limiting and ingest rate limiting (PR #87); the Deploy roadmap drafted (provisional, PR #87); the epoch batch drafts recorded (PR #84, merged) with item 8 added; the delivery-pace rulings (PR #85).

**What I learned:** CI as the test loop and a throwaway worktree for hook checks took this issue from claim to QA PASS with no human run.

## Reflection

### PM

unknown (role not asked)

### Engineer

See comment https://github.com/andrew-chung-au/socboard/issues/60#issuecomment-6815583961 (copied unchanged when this summary is next edited; not copied here to keep the issue's one PR small).

### QA

See the `Reflection:` block in comment https://github.com/andrew-chung-au/socboard/issues/60#issuecomment-6815614727.

## Launch usage

Active time and tool uses: unknown (Kiro Web gives neither). Engineer 2 (diff and proof; comments). QA 1 (PASS). Handovers: none.

## Files created or modified

`agent-kit.conf` (one line). This file.

## Decisions made by the human

- The exact `PROTECTED` diff, committed by the orchestrator with `HUMAN_APPROVED=1` (the issue said the human commits it; the human approved this route with "recommendations approved").
- Epoch effect: pending. QA called it borderline: an Engineer on an unrelated issue who changes a frontend dependency file is now refused without approval. The orchestrator's view: no move, because `AGENTS.md` already required asking before adding a dependency; this enforces an existing rule rather than adding one.

## Mismatches with the spec or design docs

none.

## Commands to validate

- **Verify:** `make verify` (CI run 43161409665: VERIFY: PASS).

## Proposed AGENTS.md changes

none.

## Temporary changes

- Two throwaway worktrees under `.scratch/` (the Engineer's and QA's), each removed with its branch; `git worktree list` shows only the main checkout.
- `make clean-scratch`: NOT run; housekeeping is on hold.

## Interruptions

none.

## Unrelated problems noticed but not fixed

- The orchestrator's claim comment named the branch base as `2c77a6f`; the branch was cut after PR #84 merged, so the base is `bdc8f7e`. QA found it; the diff of the issue itself is one line.

## Follow-ups for the next session

- #11 waits for the outside answers on its data-model rules; #12 to #14 and #86 are ready (approved or groomed).
- Decide the epoch question for #60.
