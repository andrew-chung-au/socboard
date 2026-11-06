# Check each tool's commits carry the Assisted-by line

## Purpose

Confirm that agent commits from Kiro, OpenCode and Claude Code each end with `Assisted-by: <Tool>`, as the commit-attribution decision requires, and see whether a tool adds a line of its own as well. Issue: none.

## Where to run it

In the project folder, during real Core work, after the first agent commit made by each tool. Not here: don't ask an agent for a throwaway commit just for this check. The Engineer works only on approved issues.

## Before you start

1. Confirm the rule is on main.

```
git log --oneline -1 -- _docs/decisions/2026-11-06T0111Z-commit-attribution.md
```

Expected: one commit line.

## Steps

### Step 1: Kiro

After Kiro's first commit on an approved issue:

```
git log -1 --format='%an%n%B'
```

Expected: the first line is `Kiro Agent` and the last line is `Assisted-by: Kiro`.
If different: stop, don't retry another way, and tell the orchestrator what was printed (a missing line, or a co-author line instead).

### Step 2: OpenCode

After OpenCode's first commit on an approved issue:

```
git log -1 --format='%an%n%B'
```

Expected: the last line is `Assisted-by: OpenCode`. Note whether OpenCode also added a line of its own, such as a "Generated with" line or a co-author line.
If different: stop and tell the orchestrator what was printed.

### Step 3: Claude Code

After Claude Code's first commit on an approved issue:

```
git log -1 --format='%an%n%B'
```

Expected: the last line is `Assisted-by: Claude Code`. The kit's own "Generated with Claude Code" line may also appear; note whether it does.
If different: stop and tell the orchestrator what was printed.

## Record the result

Paste the output or write pass or fail for each step. Commit this file with the result so a later run can compare. Results contain no secrets either.

| Step | Pass or fail | Output or note |
|---|---|---|
| 1 Kiro | | |
| 2 OpenCode | | |
| 3 Claude Code | | |

## Risk

None. The commands only read commit messages.
