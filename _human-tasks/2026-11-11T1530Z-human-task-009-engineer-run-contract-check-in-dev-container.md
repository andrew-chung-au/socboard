# Add the contract-check target and validator, and run them in the dev container

> Human task for #9. Written by the orchestrator; the Engineer could not run `uv` in Kiro Web.

## Purpose

Land the protected changes for #9 (the `contract-check` target, its Commands line and the validator dependency), then prove the check passes on `openapi.yaml` and fails on a broken copy. Issue: #9.

## Where to run it

The dev container, in a clean checkout of branch `issue-9-openapi-contract` (pushed to GitHub). Not here: Kiro Web, where `uv` cannot create its Python directory.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

## Before you start

1. You have read the three protected diffs in the orchestrator's message and approved them (the `Makefile` target, the `AGENTS.md` Commands line, and the dev dependency).

```
git switch issue-9-openapi-contract
```

Expected: you are on the branch, and `git log --oneline -3` shows `Add the OpenAPI contract openapi.yaml (#9)` at the top.

## Steps

### Step 1: add the validator as a dev dependency

```
uv add --dev openapi-spec-validator
```

Expected: `pyproject.toml` and `uv.lock` change; `uv.lock` names a version that supports OpenAPI 3.1 (`openapi-spec-validator` 0.7 or later). Write the version in the results table.
If different: stop and tell the orchestrator what was printed.

### Step 2: check the target and Commands line already on the branch

The orchestrator committed the approved `Makefile` target and the AGENTS.md Commands line in `2ab7135` (with `HUMAN_APPROVED=1`, after you approved the exact diffs). Check them:

```
git show --stat 2ab7135
```

Expected: only `Makefile` (6 lines added) and `AGENTS.md` (1 line added).

### Step 3: commit the dependency files

```
HUMAN_APPROVED=1 git commit -m "Add openapi-spec-validator as a dev dependency (#9)" pyproject.toml uv.lock
```

Expected: the commit succeeds with `pyproject.toml` and `uv.lock` only; end the message with the tool line the repository requires if an agent helped.

### Step 4: the check passes on the contract

```
make contract-check
```

Expected: exits 0 and says the file is valid.
If different: copy the full error to the orchestrator; do not edit `openapi.yaml` yourself.

### Step 5: the check refuses a broken copy

```
cp openapi.yaml .scratch/broken-openapi.yaml
```

Then delete the `openapi: 3.1.0` line from `.scratch/broken-openapi.yaml` and run:

```
make contract-check FILE=.scratch/broken-openapi.yaml
```

Refusal test: run it in this dev container window, not in an agent session.
Risk: none; the copy is in `.scratch/` and nothing is printed except the validator's error.
Expected: exits non-zero and names the problem. Then delete `.scratch/broken-openapi.yaml`.

### Step 6: verify is unchanged

```
make verify
```

Expected: the same steps as before; its output lists no contract check. If it fails for a reason unrelated to #9, copy the output.

### Step 7: push

```
git push
```

Expected: the branch updates on GitHub. Tell the orchestrator, who then launches QA.

## Record the result

| Step | Pass or fail | Output or note |
|---|---|---|
| 1 (validator version) | pass | `openapi-spec-validator` 0.9.0 (21 packages resolved; also pydantic 2.14.3, pydantic-core 2.50.4). Hardlink warning only. |
| 2 | pass | The branch was behind at first (`No rule to make target 'contract-check'`); after `git pull --rebase` the target and Commands line from `2ab7135` were present. |
| 3 | pass | Committed `pyproject.toml` and `uv.lock` only (532 insertions, 2 deletions); on GitHub as `2e009c9`. |
| 4 | pass | First run failed on three unquoted YAML scalars (lines 257, 592, 597), fixed in `114624c`; then `openapi.yaml: OK`, and again `openapi.yaml: OK` after the Differences change in `8ad8874`. |
| 5 | pass | A copy with the `openapi: 3.1.0` line deleted failed with a blank `Error: ` and make exit status 2; the same on the root file with the line deleted (restored with `git restore`). The failure message is blank, which is a known limitation. |
| 6 | pass | `VERIFY: PASS`: 47 Python tests, 154 frontend tests, no whitespace errors, "No test files changed". Its output lists no contract check. |
| 7 | pass | The dev container cannot push (403 by design). Pushed from the clean clone after building `agent-push-check:node24-uv0.13.6-bun1.4.2` (bun 1.4.2) and setting `AGENT_PUSH_CHECK_IMAGE`; the push check passed. Also covers the rebuild-the-image task from #46. |

Filled in by the orchestrator from the output the human pasted in this session; the human confirmed the steps ("I think I did the steps can you fill it in for me").

## Risk

The commit changes protected files; it is committed only with your approval of the exact diff. Nothing else changes. Undo: `git reset --hard HEAD~1` on the branch before pushing.
