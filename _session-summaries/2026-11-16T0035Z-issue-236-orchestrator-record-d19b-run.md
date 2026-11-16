# Issue #236: D19b, the record of the run and the steps for the next replacement

**Date:** 2026-11-16T00:35Z (UTC)
**Spec version:** e3461a7

## What changed and why

The maintainer ran D19b on 2026-11-16 from the planning session's steps, before any checklist for #236 existed. This session (Terraform lane A, taken over from an earlier Claude session that had left no claim and no branch) wrote the record and the reusable steps.

- `_human-tasks/2026-11-16T0033Z-human-task-236-engineer-first-apply-part-b.md`: the record of that run, with the results the maintainer reported. The plan was 4 to add, 0 to change, 3 to destroy, one add above the body's expectation because `aws_ssm_document.deploy` (#237) was created in the same apply. Rows the run did not cover are NOT CHECKED, not invented.
- `terraform/README.md`, "Steps for the human in D19b": rewritten in the step format for the next replacement of a bootstrapped instance (#266). Steps 1 and 2 take readings before the replacement; step 6 updates `DEPLOY_INSTANCE_ID`; the plan step says a later replacement reads 3/0/3 once the document exists.

**What I learned:** the commands of the 2026-11-16 run were not given to this session, only its results. The steps are the README's earlier commands, corrected and written out; the maintainer's actual commands were not compared.

## Reflection

unknown (role not asked)

## Launch usage

none

## Files created or modified

- `_human-tasks/2026-11-16T0033Z-human-task-236-engineer-first-apply-part-b.md` (new)
- `terraform/README.md` (the D19b subsection, and the bootstrap section's status paragraph and four settled `VERIFY:` notes: see "Decisions made by the human")
- `_docs/records/findings/236.json` (new)
- this file

## Decisions made by the human

- The maintainer reassigned #236 to this session (2026-11-16) and said the run was already done, so the checklist is a record and the human is asked to run nothing.
- Scope addition, approved by the maintainer on 2026-11-16 (a change to #236's "no other README section changes" constraint): update the bootstrap section's "Status: not yet run" and the `VERIFY: at D19b` notes that the run settled, and no others. Settled and removed: the package names on noble, the by-id link name, the re-run path (and its idempotency), plus the status text (packages installed, the volume found and mounted, `caddy/` on the volume, reboot recovery). Left open: the Cloudsmith key, unattended upgrades, the hostname format, Let's Encrypt without a contact address, a missing volume not blocking the boot, certificate re-use on a replacement, and the forwarded address.
- Results: rows 12 to 14 stay NOT CHECKED (no reading was taken before the replacement); the reboot and second-plan rows read PASS (as reported); everything else not reported reads "Not reported".
- The maintainer gave the commands of the run; the README steps were rewritten to follow them (sign in, plan with checks, apply of the saved plan, `send-command` checks, re-run, reboot, second plan, `rm -f d19b.plan`). New and marked as such: the readings before the replacement, the certificate comparison, the `ssm_run` helper and the `DEPLOY_INSTANCE_ID` update. The account check is my wording of "compare without printing" (`VERIFY:` `sso_account_id`).

## Mismatches with the spec or design docs

#236's body asks for a checklist for stage (a) and a run at stage (b); the run happened first. The body still describes the earlier plan (3/0/3) and the full row list; this change records the difference rather than editing the body.

## Commands to validate

**Verify:** `make verify` (CI; not run in this sandbox, which has no writable uv cache).

## Proposed AGENTS.md changes

none

## Temporary changes

none

## Interruptions

none

## Unrelated problems noticed but not fixed

- F-236-1: the bootstrap section's "Status: not yet run" text and "VERIFY: at D19b" notes were out of date after the run. Fixed in this pull request on the maintainer's approval (the findings file is not edited, per its rule).
- F-236-2: no readings were taken before the replacement, so three comparison rows cannot be filled.
- F-236-3: the outside checks and the listener's peer address were not run against the new instance.

## Follow-ups for the next session

- #266 groomed with the maintainer's changes; the maintainer labels it.
