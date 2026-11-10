# Measurement: start of ponytail epoch 1

One file per repeatable check of quality or cost. This one fixes the starting point of the trial's first epoch (`_docs/planning/ponytail-trial-protocol.md`, section 3), so later sweeps can tell whether anything that could change a control issue moved.

- **Question:** From which commit, and with which kit, role files and skill pin, do Phase 1 baseline issues count?
- **Command and commit:** `git rev-parse --short HEAD:<path>` and `git log -1 --format=%h -- <path>` for each file below, at `a1d37ba` (merge of PR #33, 2026-11-10T15:05Z). `KIT_VERSION` read from `agent-kit.conf`.
- **Baseline:** none; this is the first epoch.
- **Result:**
  - **Epoch 1 starts at `a1d37ba`.** The open human tasks were recorded first (PR #33): Claude Code layer installed (9b69b14); commit lines checked for Kiro and Claude Code, OpenCode not run; stack record name kept; upstream finding filed as agent-kit#187.
  - **Kit:** `KIT_VERSION="1.10+socboard.1"`.
  - **Protocol:** version 1, registered in 1c5e8f4 (2026-11-06); file blob `9ba1bbb`, last change fcc837d.
  - **Metrics script:** `scripts/ponytail_metrics.py` blob `8a24697`, last change 8330f05.
  - **Skill pin:** `_docs/vendor/ponytail/ponytail-review.md`, blob `a50e3ac` (upstream `main` at `96608e0`, version 5.3.3; source record `_docs/records/2026-11-06T0024Z-source-ponytail.md`). The core `ponytail` skill is not installed.
  - **Files that, if changed, start a new epoch** (blob, last change):
    - `AGENTS.md` `66890a9`, 66eab17
    - `_docs/agent-kit/process.md` `13d3060`, 6211f95
    - `_docs/agent-kit/team/pm.md` `09c93cd`, 6211f95
    - `_docs/agent-kit/team/software-engineer.md` `24fd678`, 6211f95
    - `_docs/agent-kit/team/qa-engineer.md` `e9e5c04`, 35e09b3
    - `_docs/team/reviewer.md` `6f61b25`, 4f33407
    - `.claude/agents/pm.md` `b5dc48d`, `.claude/agents/software-engineer.md` `db6cc72`, `.claude/agents/qa-engineer.md` `2b07662` (all 9b69b14)
    - `.claude/agents/reviewer.md` `1851c97`, `.opencode/agents/reviewer.md` `30c8693` (both dbde225); `.kiro/agents/reviewer.json` `5e91127`, 5fc032c
  - **Excluded issues:** #7 and #27, the Lovable scaffold (protocol section 3). #23, #24 and #25 are trial plumbing.
  - **First counted issue:** #8 (human decision 2026-11-10), as a control issue: its Constraints carry no `ponytail` line.
- **Moved (sweep 1, 2026-11-10):** the epoch start is now `17e725e`, which changed `_docs/agent-kit/team/pm.md` (blob `17a442c`) and `_docs/agent-kit/team/software-engineer.md` (blob `8e79bf1`). `_docs/agent-kit/procedures/orchestrator-comments.md` also changed but is not on the list. No data was lost: nothing had been measured. The blobs in the list above for those two files are superseded by these. See `_docs/records/2026-11-10T1518Z-measure-pm-sweep.md`.
- **Caveats:**
  - **Provisional until #8's first Engineer launch.** If a change to a file above lands before then (for example from the first sweep, or #8's re-grooming touching `AGENTS.md`), the epoch start moves to that commit. Nothing has been measured yet, so moving it loses no data. Record the move in the next session summary.
  - #8 was groomed on 2026-11-09, before this start. The handoff recommends re-grooming it, which also puts its grooming after the start.
  - The commit-line check has not run for OpenCode. It reads commit messages only, so it does not change agent behaviour.
  - #23's checks that OpenCode and Claude Code can read the pinned file were recorded "not tested". Phase 1's gate needs every tool used to load it; confirm before the first shadow review.
  - Two #27 commits carry a wrong `Assisted-by: Kiro` (the human made them). #27 is excluded, so no counted figure is affected.
