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

### Step 2: add the Makefile target and the AGENTS.md Commands line

Apply the approved diffs: a `contract-check` target in `Makefile` that runs `uv run --locked openapi-spec-validator` on `openapi.yaml` (or on `FILE=<path>` when given, checked with the existing `kit-path-ok`), and one line in the AGENTS.md Commands list: "**Contract check:** `make contract-check`: validates `openapi.yaml` as OpenAPI 3.1; fails on an invalid file."

Expected: `git diff --stat` lists `Makefile`, `AGENTS.md`, `pyproject.toml` and `uv.lock` only.

### Step 3: commit the protected files

```
HUMAN_APPROVED=1 git commit -m "Add make contract-check and the OpenAPI validator (#9)"
```

Expected: the commit succeeds with the four files; end the message with the tool line the repository requires if you used an agent.

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
| 1 (validator version) | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |

## Risk

The commit changes protected files; it is committed only with your approval of the exact diff. Nothing else changes. Undo: `git reset --hard HEAD~1` on the branch before pushing.
