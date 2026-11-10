# Issue #27: Make the frontend build static and remove Lovable coupling

**Date:** 2026-11-10T14:32Z (UTC)
**Spec version:** d913e19
**Tier:** Standard (PM proposal)

## What changed and why

- The spike (run by the human): TanStack Start's single-page mode still emitted Nitro and Cloudflare worker output and no `index.html`, so the fall-back was taken. TanStack Start, Nitro and the Lovable build wrapper are removed, and `frontend/` builds with plain Vite and React plus TanStack Router to `frontend/dist`.
- Removed: the Lovable files and text, the Google Fonts links, `chart.tsx` and 37 unused shadcn files, `use-mobile.tsx`, the Start server files, and 36 packages. Added (the human approved): `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono` 5.3.0 (OFL-1.1), Latin subset at weights 400, 500 and 600.
- The mock is chosen by a literal `import.meta.env.VITE_USE_MOCK !== "false"`, so a production build drops it. The error page shows the error as escaped text, with a test.
- New make targets `frontend-install` and `frontend-build` (protected Makefile, `HUMAN_APPROVED=1`); `frontend-build` fails on a missing `index.html`, server or worker output, the demo credentials or the `<script>alert(` fixture, or a Google fonts host. Their two Commands lines were added to `AGENTS.md` (approved).
- The dev container did not have bun, although `bun.lock` is the lockfile (the open package-manager question defaulted to bun). The human chose to keep bun; `.devcontainer/setup.sh` now installs bun 1.4.2 (approved).
- The Engineer work was done in Kiro Web, which cannot reach the npm registry (HTTP 403); the human ran every install, build, test and browser check. QA (Claude Code) first returned FAIL because its environment had no bun; after the container change it posted PASS at `ecf7965` (comment 4 on #27), with the refused cases run end to end in a `.scratch/` copy. `make assert-clean` passed in the container with HEAD `ecf7965`.
- Follow-up #31 filed (frontend lint: 114 prettier errors in the import).

**What I learned:** check the QA environment's tools before planning QA. The recipe said npm while the lockfile said bun, and the PM had flagged it; reading `.devcontainer/` at the start would have saved one QA round.

## Reflection

### Orchestrator

**What slowed you down (evidence):** the Kiro Web sandbox has no registry access, so every build step became a round of instructions and pasted output. One step served the wrong folder (`dist/client`); later the dev container turned out to lack bun, which cost a QA round.
**What nearly went wrong or was blocked:** I had the human run `make` and `bun install` in the clean clone (`~/clone/socboard`), which `environments.md` forbids; a pasted diff was run as shell commands. I also once ran inline `python3 -c` instead of a `.scratch/` script.
**One proposed change (file, expected effect, measure):** `_docs/agent-kit/procedures/verify.md` or the handoff template: before an issue whose checks need tools (bun, Node, Docker), list the tools each environment (agent, QA, clean clone) has. Expected effect: no QA round lost to a missing tool and no checks run in the clean clone. Measure: QA FAILs caused by the environment, and `make` runs in a clean clone.

### PM

unknown (role not asked)

### Engineer

unknown (role not asked; the orchestrator did the Engineer work)

### QA

unknown (role not asked)

## Launch usage

- Engineer work for #27 by the orchestrator in Kiro Web (model: auto), not a separate launch: none.
- QA, #27 first run, Claude Code 2.1.322: active time unknown, tool uses unknown; model unknown. Verdict FAIL (environment blocked; no defect).
- QA, #27 second run, Claude Code 2.1.322: active time unknown, tool uses unknown; model unknown. Verdict PASS at `ecf7965`.

## Files created or modified

- `frontend/`: `index.html`, `src/main.tsx`, `src/vite-env.d.ts`, `src/components/soc/ErrorPage.tsx`, `src/test/error-page.test.tsx` (new); `vite.config.ts`, `package.json`, `bun.lock`, `bunfig.toml`, `.gitignore`, `eslint.config.js`, `AGENTS.md`, `README.md`, `src/router.tsx`, `src/routeTree.gen.ts`, `src/routes/__root.tsx`, `src/routes/login.tsx`, `src/routes/README.md`, `src/services/config.ts`, `src/services/index.ts` (modified); 45 files removed (listed on #27).
- `Makefile`, `AGENTS.md`, `.devcontainer/setup.sh` (protected, approved).
- `_docs/planning/lovable-guidance.md`, `_docs/records/2026-11-10T0946Z-source-lovable-frontend.md`.
- `_human-tasks/2026-11-10T1205Z-human-task-027-engineer-spa-mode-spike.md` (spike steps and results).
- This file.

## Decisions made by the human

- Take the plain-Vite fall-back after the spike.
- Agent edits in Kiro Web; the human runs builds and tests. Push to the branch approved.
- Makefile targets approved; font packages approved; `AGENTS.md` Commands lines approved.
- Keep bun (option A) and install it in the dev container; the `setup.sh` diff approved.
- A follow-up issue for lint (#31).
- Open a pull request for the merge.
- Not answered at the decision point (recorded with the orchestrator's recommendation): delete `/tmp/bunx-1000-frontend@latest` in the container (recommended); test the error page through the router in #8; accept the Latin-only font subset; note the grep-bypass limit of the bundle check in #8; record bun for CI and the push-check image for Deploy; whether to run the first sweep after #27 (due: none recorded); whether #27 counts as a ponytail baseline issue (left as not counted).

## Mismatches with the spec or design docs

- `.devcontainer/setup.sh` previously said the project's lockfile tool is npm; the frontend uses bun. Fixed in this issue.
- Deep links (`/cases/<id>`) return 404 on a plain static server; the server must fall back to `index.html`. A Deploy-phase need, not solved here.

## Commands to validate

**Verify:** `make verify` (PASS in the human's runs and in QA's).
**Frontend install:** `make frontend-install`
**Frontend build:** `make frontend-build`

## Proposed AGENTS.md changes

none outstanding (the two Commands lines were approved and committed in `66eab17`).

## Temporary changes

- The human ran `bun install`, `bun add`, the builds and `make` in the clean clone `~/clone/socboard`; `frontend/node_modules` stays there (git-ignored). Spike output (`.output`, `.wrangler`, `dist`) was removed.
- QA's mistyped `bunx` left `/tmp/bunx-1000-frontend@latest` in the container, outside the repo; deletion is the human's (not yet confirmed).
- No process is left running; the human stopped each `python3 -m http.server`.
- `make clean-scratch` ran in the Kiro Web sandbox at the end of the session. QA's `.scratch/qa27/` in the container is cleared by `make clean-scratch` there (the human's).

## Interruptions

- QA's first run returned FAIL because the container had no bun and no `node_modules`; `make assert-clean` passed and HEAD had not moved, so it was a valid verdict. It was re-run after the container change and passed.

## Unrelated problems noticed but not fixed

- `bun run lint` fails on the import's formatting (#31).
- The kit's reference `environments/local-sandbox/setup.sh` still has no bun; this project's copy now differs from it.
- CI (`.github/workflows/verify.yml`) and the push-check image have no bun; nothing in them builds the frontend yet.

## Follow-ups for the next session

- After the merge: the Close comment on #27 and `make issue-close N=27`.
- The decisions above that were not answered.
- #8 next (mock rules, fixtures, dev-server and test targets, the README environment section); include the router-level error-page test.
- Approve or rework #31; it should follow #8.
