# Check make frontend-lint and make frontend-format

> Drafted from `_docs/agent-kit/templates/human-task.md`. No step prints a secret or the environment.

## Purpose

Check by hand the criteria of #75 that need `frontend/node_modules` and the npm registry: the refused case, the pass-through of bun's result, the format run, the missing-tool message in a clone, and that `make verify` is unchanged. Issue: #75.

## Where to run it

The dev container, in a checkout of branch `issue-75-frontend-lint-format` with the #75 commit, after `make frontend-install` (except Step 5, which needs a clone without `frontend/node_modules`). Not here: `main`, for any step that writes (Step 5). `make frontend-format` rewrites files, so never run it on this branch or on `main`.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

## Before you start

1. Confirm you are on the issue branch with a clean tree.

```
git status --short --branch
```

Expected: the first line names `issue-75-frontend-lint-format`; no other lines.

## Steps

### Step 1: Create the untracked probe file with single quotes

Create `frontend/src/lint-probe.ts` containing exactly this one line (single quotes):

```
export const probe = 'x';
```

Expected: `git status --short` shows `?? frontend/src/lint-probe.ts` and nothing else.
If different: stop and tell the orchestrator what was printed.
Risk: creates one untracked file; Step 3 deletes it.

### Step 2: The lint target refuses the probe and takes no arguments

```
make frontend-lint
```

Expected: a `prettier/prettier` error for `frontend/src/lint-probe.ts` (among the other errors current `main` has until #31), and a non-zero exit.

```
make frontend-lint ARGS=--fix
```

Expected: the same error for the probe file, a non-zero exit, and `frontend/src/lint-probe.ts` unchanged (still single quotes; the target ignores `ARGS`).
If different (the file was rewritten or `--fix` took effect): fail; tell the orchestrator what was printed.
Risk: if the rule fails, `--fix` would rewrite only the untracked probe file; nothing tracked is touched.

### Step 3: Delete the probe and check the tree is clean

Delete `frontend/src/lint-probe.ts` (the file is this task's own).

```
git status --short
```

Expected: nothing printed.

### Step 4: Pass-through comparison for lint

```
make frontend-lint
```

```
cd frontend && bun run lint
```

Expected: the same rules, counts and files in both, and the same non-zero exit on current `main` (it has lint errors until #31). The counts differ from older numbers because #21's frontend changes landed after the issue was groomed; compare the two runs with each other, not with old figures. The `make` run adds only the `make: *** ... Error` line.
If different: fail; paste both outputs to the orchestrator.

### Step 5: The format run, on a throwaway branch only

Do not run this on `issue-75-frontend-lint-format` or `main`. Create the throwaway branch from the current one:

```
git switch -c throwaway-75-format
```

```
make frontend-format
```

```
git status --short
```

Expected: changed files listed under `frontend/` (the target rewrites files). Write down whether the list includes `frontend/package.json` (a protected file) or any file under `frontend/src/test/` or any `*.test.*` file (the weakened-test guard watches those); tell the orchestrator either way. Then discard them and leave the branch:

```
git restore frontend
```

```
git status --short
```

Expected: nothing printed.

```
git switch issue-75-frontend-lint-format
```

```
git branch -d throwaway-75-format
```

Expected: you are back on the issue branch and the throwaway branch is gone.
If different: do not retry another way; tell the orchestrator what was printed.
Risk: `make frontend-format` rewrites tracked files under `frontend/` (markdown, CSS, JSON, HTML and sources not in `frontend/.prettierignore`). Undo: `git restore frontend`. Files Prettier creates, if any, show as `??` in `git status --short`; remove only those you recognise as its output.

### Step 6: The missing-tool message in a clone without node_modules

In a fresh clone of the branch (no `frontend/node_modules`; do not run `make frontend-install` there):

```
make frontend-lint
```

Expected: `frontend-lint: frontend/node_modules/.bin/eslint is missing; run make frontend-install first`, a non-zero exit, and no bun output.

```
make frontend-format
```

Expected: `frontend-format: frontend/node_modules/.bin/prettier is missing; run make frontend-install first`, a non-zero exit, and no bun output.

### Step 7: make verify is unchanged

On this branch, then on `main` (both after `make frontend-install`):

```
make verify
```

Expected: the same list of steps and the same kind of output on both. Neither lint nor format appears among the steps.
If different: fail; paste the two step lists to the orchestrator.

## Record the result

Paste the output or write pass or fail for each step. Commit this file with the result so a later run can compare. Results contain no secrets either.

| Step | Pass or fail | Output or note |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |

## Risk

Step 1 creates one untracked file (removed in Step 3). Step 5 rewrites tracked files under `frontend/` on a throwaway branch; `git restore frontend` undoes it. `make frontend-format` rewrites files, so it must not be run on this branch or on `main`. Nothing else changes live behaviour.
