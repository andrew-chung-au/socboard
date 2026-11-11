# Runbook: a step needs tools that Kiro Web does not have

Written 2026-11-11 at `26bb7d8`. Read it before launching a role on a frontend or backend issue, and when a QA or Engineer comment says "not run". It does not copy commands from `AGENTS.md`; it says where each step can run.

- **Symptom:** an Engineer or QA comment says `make test`, `make verify`, Vitest, a dev server or a package install "did not run", or a criterion reads "not checked".
- **Check:** the table below. Every row was observed in a Kiro Web session on 2026-11-10 and 2026-11-11 unless it says unverified.
- **Fix:** hand the branch to the dev container, following "The handover" below. Kiro Web does everything that does not need the missing tools.
- **Undo:** none needed; a handover changes no files. If a handover step is skipped, the criterion stays "not checked" and the issue is not closed on it.

## What each place can do

| Capability | Kiro Web | CI (GitHub Actions, on every push) | Dev container (local sandbox) | Your machine |
|---|---|---|---|---|
| Read, edit, commit, push a branch, open a PR, post issue comments | Yes | n/a | Commit yes; push no (read-only token) | Yes (review and push) |
| `python3 -m pytest` on the Python tests | Yes, on Python 3.11 (the project needs 3.12). 47 tests passed on 2026-11-11. `fastapi` and `sqlalchemy` are NOT importable | Yes | Yes | Yes |
| `make test`, `make verify` | No: `uv` cannot create its Python directory (read-only file system), and Python is 3.11 | Yes: runs `make install` then `make verify` with bun 1.4.2 and Node 24.22.0; verified on PR #47 | Yes (needs `make install` once) | Yes |
| `make install`, `make frontend-install` | No: the npm registry returned HTTP 403 (#27, #46). The Python package index is unverified from here | Yes | Yes (registry access is the reason to use it) | Yes |
| Vitest, `make frontend-test`, `make frontend-build`, `bun run lint` | No: `frontend/node_modules` cannot be installed. bun 1.2.14 exists there; the lockfile was written by 1.4.2 | Yes (`frontend-test` via `make verify`; build and lint are not in CI) | Yes | Yes |
| A real dev server (`make frontend-start`), a browser | No | No | Server yes; browser depends on your port forwarding | Yes |
| Claude Code, OpenCode (the other agent tools) | No | No | Yes, installed by `.devcontainer/setup.sh` | Whatever you install |
| Docker (push-check image build, Compose) | No | n/a | No | Yes |
| Read a CI log | Yes: `gh api repos/<owner>/<repo>/actions/jobs/<id>/logs` worked on 2026-11-11 | n/a | Yes | Yes |

## What this means

1. **CI is the verification that does not need a handover.** Pushing a branch from Kiro Web runs `make install` and `make verify` with registry access. Read the log and cite the run id as evidence. This covers: the full gate, the Python and frontend test counts, and the whitespace and weakened-test checks.
2. **CI is not QA.** QA's own mutations, attack scripts and refused-case checks need a working tree with the tools. A QA launch in Kiro Web on a frontend or backend issue can read code and CI logs but cannot run or mutate the tests. Run those QA launches in the dev container.
3. **Backend issues cannot be run in Kiro Web at all** (`fastapi` and `sqlalchemy` are missing, Python is 3.11). Writing and review can be done there; any run needs CI or the dev container.
4. **Handover waits inflate the time metrics.** The trial's elapsed-time figures come from GitHub timestamps and "include idle time". Post a handover comment with a timestamp at each hand-off and hand-back (see below), so a later reader can separate working time from waiting.

## Per-issue map (at `26bb7d8`; revisit when an issue is groomed)

| Issue | Kiro Web | Hand to the dev container | Why |
|---|---|---|---|
| #8 (mock and service-layer rules, tests only) | Engineer writes the tests and commits; push, then read the CI run | Run `make install`, `make frontend-test`, `make verify`; QA fresh launch with mutations (for example reintroducing `dangerouslySetInnerHTML` in a copy) | The criteria are test results; nothing in Kiro Web can run Vitest |
| #51 (timer, reassign flag, component tests, README) | Engineer writes code and tests; CI run as evidence | The same as #8, plus `make frontend-start` and a look at the timer in a browser (you) | Real timing and the page are visible only with a running server |
| #9 (OpenAPI contract) | Write and review the YAML; read-only checks | Any validator run (none is installed in Kiro Web: `yaml` and `jsonschema` are not importable) | A validator criterion cannot be checked in Kiro Web |
| #10 to #14 (FastAPI skeleton, data model, scoring, auth, ingest) | Write and review code | Everything that runs: `make install`, `make test`, `make verify`, the app | `fastapi`, `sqlalchemy` missing and Python 3.11 in Kiro Web; CI can run the tests but not QA's attacks |
| #31 (`bun run lint` passes) | Edit the code | Run the linter | Needs `node_modules`; CI does not run lint |
| #21 (connect frontend to the real API) | Write code | Run both servers and the journeys | Needs the backend and the frontend running together |
| #22 (integration tests, one-command run) | Write code | Run it on your machine if it uses Docker | Docker is available only on your machine |
| Trial: reviewer reads the pinned file in OpenCode and Claude Code (#23 recorded "not tested") | Kiro's own pointer can be tested here | Test the other two tools in the dev container | Only the dev container has those tools |
| Trial: cross-tool calibration (same diff, two tools) | One of the two tools | The other tool | The protocol needs two tools on the same diff |

## The handover

When a role in Kiro Web reaches a step it cannot run, it stops there. The orchestrator then does the following, in this order.

1. **Push the branch** (`issue-<n>-<slug>`), so CI runs. Record the commit and the CI run id in an `Orchestrator:` comment on the issue.
2. **Leave a handover comment** on the issue, headed "Handover to the dev container", with:
   - the branch and the exact commit;
   - what ran in Kiro Web and what did not, with the reason (use the table above);
   - the commands to run, as `make` targets in order, for example `git fetch`, `git checkout <branch>`, `make install`, `make verify`, and the criteria each one settles;
   - what to report back: the command output (counts, `VERIFY:` line), the commit hash tested, and the tool and model used;
   - what the receiving role must not do: change files, push, close or relabel; QA changes nothing outside `.scratch/`;
   - the time the handover was posted.
3. **The dev-container side** runs the commands, then posts a hand-back comment with the output and its own time. A QA launch there follows `qa-engineer.md` as usual, including `make assert-clean`.
4. **The orchestrator continues** from the hand-back: the hash check, the next role, or a QA FAIL back to the Engineer.
5. **Record it.** The session summary gets one line per handover (issue, step, who ran it, tool and model, wait time), so the trial can annotate or exclude the waiting time.

## What stays the same everywhere

- Only the human labels, merges, rebuilds the image and pushes the clean clone.
- A criterion that no one has run reads "not checked" or "human-verified", never PASS.
- The tool and the model are recorded at launch, not afterwards, for every role.
- A QA launch is fresh and independent. A hand-back from the dev container is evidence for the criteria it names, not a replacement for QA's own checks.
