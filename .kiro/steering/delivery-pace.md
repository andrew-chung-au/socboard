---
inclusion: always
---

# Delivery pace (human ruling, 2026-11-12)

Applies to the orchestrator in this repository. The human ruled these after #9, #10 and #74 took two to four human hand-overs each.

1. **CI is the test loop.** Push the issue branch and open a draft pull request early; CI runs `make install` and `make verify` on every push, and on a pull request its weakened-test check compares with `main`. Read results with `gh api repos/<owner>/<repo>/actions/runs?branch=<branch>`, then `.../actions/runs/<id>/jobs` and `.../actions/jobs/<id>/logs`. QA cites the run id and the log lines as evidence ("CI-VERIFIED"). Ask the human to run only what CI cannot: `uv add` (the lockfile), starting servers, a browser, Docker.
2. **One pull request per issue.** Commit the session summary, and any roadmap or supporting-list update, on the issue branch before the human merges. A separate pull request only for work outside any issue.
3. **Groom ahead in batches.** While the current issue waits, the PM may check or groom the next two or three Core issues; the human approves them in one sitting. This overrides "groom only the next issue" for this repository.
4. **One extra issue in flight**, limited to small supporting issues (for example #60, #62), while the main issue waits on the human or an outside reviewer. Never two Engineer launches on Core issues at once.
5. **Light tier for docs-only changes** is queued for the epoch batch (`LIGHT_ALLOWED` in `agent-kit.conf`, protected); until it lands, docs changes follow the normal path.
6. **The Heavy second QA launch is a new session** (interim rule until sweep 4 decides the kit change, `_docs/planning/sweep-4-candidates.md`, item 1). Its launch message tells the reviewer: "If this session has already reviewed, implemented or discussed this issue, stop before reviewing and say so." When the result comes back, check its comment states a new session; a launch that does not is discarded and run again (`process.md`, Tiers). A fresh subagent with no shared context also counts (the human allowed it on #13, #99 and #14). The launch message tells a blind second launch to exclude `.scratch/qa-*`, `.scratch/engineer-*` and `.scratch/issue-*.json` from every search, or to limit searches to `socboard/**` and `tests/**`, and to read the issue body only through a body-only extract (sweep 5, item 5; on #14 a broad search leaked earlier QA lines).
