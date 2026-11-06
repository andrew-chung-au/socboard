# Decide: rename the stack decision record to the kit's timestamp format

## Purpose

The record `20261105T133030Z-stack-choice.md` does not use the kit's timestamp format (`YYYY-MM-DDTHHMMZ`). Decide whether to rename it. Blocks: nothing. It is cheaper now than after issues and logs reference it. Issue: none.

## Where to decide

In the orchestrator session. Nothing to run.

## Options

1. Rename it to `2026-11-05T1330Z-stack-choice.md` in one commit, updating every reference. Several docs mention it, including the protected `AGENTS.md`, so the commit needs your approval.
2. Leave the name and accept the inconsistency.

## Recommendation

Option 1, now, before the first issues exist.

## Record the result

Write Pass once decided, and name the option in the note.

| Step | Pass or fail | Output or note |
|---|---|---|
| Decide | | |

## Risk

A missed reference becomes a broken link. A search for the old name after the rename shows any that remain.
