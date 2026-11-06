# Source: ponytail (agent skill)

One file per external input: a dataset, third-party API, vendor doc, standard or tool version.

- **Location:** https://github.com/DietrichGebert/ponytail (canonical repo; npm package `@dietrichgebert/ponytail`).
- **Version or date retrieved:** 5.3.3 (`package.json`), `main` at commit `96608e078b2d5ea607c5c3b8b5a54d1c1fba6846` (committed 2026-10-29). Retrieved 2026-11-06 through the GitHub API.
- **Why included or excluded:** Included as the subject of the trial in `_docs/decisions/2026-11-06T0024Z-ponytail-trial.md`. Only its skill files are to be vendored, at this pin. Its plugins are excluded.
- **Known quirks:**
  - *Read directly:* README, INSTALL.md, `docs/agent-portability.md`, `skills/ponytail/SKILL.md`, `.kiro/steering/ponytail.md`, `hooks/claude-codex-hooks.json`.
  - *Not read:* the `ponytail-review`, `-audit`, `-debt`, `-gain` and `-help` skill files, the OpenCode plugin code, the Node hook scripts, and the benchmark method files. Check that `ponytail-review` is self-contained before vendoring it.
  - *Benchmark figures* (roughly half the code, more risky logic tested) are the vendor's own, from Claude Code on one model family. They are unverified here and not assumed to transfer.
  - *Auto-trigger:* the core skill's description invites loading on any coding task. We vendor it verbatim, so scoping relies on our own rule, not on editing the file.
  - *Kiro:* upstream ships an always-on steering file with no commands or levels. We use a skill, or manual-inclusion steering as a fallback.
  - *Plugins:* the Claude Code and Codex forms run Node lifecycle hooks, including one that injects into subagents. The OpenCode form injects its rules every turn. The Claude Code "band" keeps data under the user's home config. None is used.
  - *Lookalikes:* several forks and copies exist with different claims. The README says to install only from the canonical repo or npm package.
- **Licence:** MIT.
