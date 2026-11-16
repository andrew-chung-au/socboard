# Rehearse the restore, run one deploy and one rotation by hand, and read the teardown

## Purpose

Stage (b) of #240: prove the runbook `_docs/runbooks/deploy-and-teardown.md` on the real server, and record the result in this file. The steps themselves are in the runbook (one command per block, `Expected:` and `If different:`); this file says which ones to run, in what order, and what to write down. Issue: #240.

## Where to run it

Your own machine, with your own AWS credentials for the account (SSO, region `us-east-1`), in a clone of the repository on `main` with the runbook merged. Steps on the server run in a Session Manager session, as the runbook says. Not here: the agent's sandbox, which has no AWS access. The agent never runs these steps. Every destructive step (deleting the scratch volume) is the human's only.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

Values used below, never copied into this file: `<instance id>`, `<hostname>` and `<data volume id>` are outputs of `terraform/server/`; `<digest>` is read in the runbook's Step 2.2.1; `<secret file>` is a path outside the repository. This file holds no account number, no ARN, no real IP address, no e-mail address and no secret value.

## Before you start

1. Dependencies, each with what it protects. The first dev deploy (#237 stage (b), `_human-tasks/2026-11-15T1822Z-human-task-237-engineer-first-deploy.md`) has run: without it there is no database to restore and no `Expected:` seen for the runbook's deploy steps. At least one daily snapshot exists (the first at 03:00 UTC after the volume exists, `terraform/iam-and-budgets/main.tf`).

```
date -u +%Y-%m-%dT%H:%M:%SZ
```

Expected: the current UTC time. Write it in the table below as the start; write the time again at the end of the rehearsal, so the table holds how long it took.
If different: none.

## Steps

### Step 1: the restore rehearsal

Run the runbook's section 7.1, Steps 7.1.1 to 7.1.12, in order, on a scratch volume made from the newest snapshot. Record: the date, the snapshot's age (from Step 7.1.1), the result of each step (pass, fail or not checked), the time it took, the value of the Postgres version file (Step 7.1.6) and the row count (Step 7.1.8).

Expected: each step's `Expected:` as the runbook states it; the row count is a whole number; the scratch volume is deleted at Step 7.1.12 and the check before it shows no `project` tag on it.
If different: stop at that step, record what was printed, and tell the orchestrator. The fix goes through a new issue.
Risk: a second volume is attached to the live instance and deleted at the end. The live volume and the running stack are not touched; the table has a row saying so. Do not reboot the server while the scratch volume is attached (runbook, section 7).

### Step 2: one routine deploy and one failed-run read, by hand

Run the runbook's Steps 2.2.1 to 2.2.4 (the newest digest, then the same digest again) and Step 2.3.2 (the digest of 64 zeros). Expected: `socboard-deploy: already deployed. Nothing was changed.` or `socboard-deploy: deployed.`, then exit 4 and its fixed text from the runbook's section 3 table. Record whether the output matched the table in section 3, and replace the runbook's unseen `Expected:` lines with what you saw.
If different: stop and record what was printed.
Risk: a deploy of a new digest swaps the app (a short outage); the zero digest changes nothing.

Refusal test: Step 2.3.1 of the runbook (the value `latest`), in your own machine's terminal, not in an agent session. Risk: if the pattern did not hold, the script's own check refuses it with exit 2; nothing changes and no secret is printed.

### Step 3: one rotation, end to end

Rotate `SOCBOARD_INGEST_API_KEY` (the runbook's 6.2), using Steps 6.0.1 to 6.0.4 and then a deploy of a different digest or section 6.8's procedure. Record what the app does before and after (an ingest with the old key and with the new one) and that no value was printed.

Expected: the check in 6.2 passes and no step printed a value.
If different: stop; the secret's previous version is restored by putting the previous file the same way (the runbook's Undo for section 6).
Risk: changes the live secret and restarts the app; a sender holding the old ingest key gets 401 afterwards.

### Step 4: the teardown is read, not run

A second person, or you on a later day, reads the runbook's section 9 against `terraform/README.md`, "Teardown (the human's step, destructive)" of each part, and records whether the counts and the order match. Nothing is run. Running the teardown is your decision at the end of the project and is recorded on its own.

Expected: each part's destroy count in the runbook equals the README's (server 10, IAM and budgets 8, CI access 7 or 6, state 6).
If different: record the difference; the runbook is corrected in a new issue.
Risk: none, it only reads.

```
git status --short
```

Expected: only this file and the evidence timeline show as changed once the table is filled in. Commit them; the timeline line for #240 is already there as pending and is updated, not added again.
If different: a listed file you did not mean to change: leave it out of the commit.

## Record the result

Paste the output or write pass or fail for each step. Commit this file with the result so a later run can compare. Results contain no secrets. `NOT CHECKED` stays until the step is run.

| Step | Pass or fail | Output or note |
|---|---|---|
| 0. Date, start time and end time of the rehearsal | NOT CHECKED | |
| 1. Newest snapshot's age at the rehearsal | NOT CHECKED | |
| 2. Rehearsal steps 7.1.1 to 7.1.5 (list, zone, create, attach, mount) | NOT CHECKED | |
| 3. Postgres version file's value (Step 7.1.6) | NOT CHECKED | |
| 4. Throwaway container started, log shows recovery and ready (Step 7.1.7) | NOT CHECKED | |
| 5. Row count of `cases` (Step 7.1.8) | NOT CHECKED | |
| 6. Clean-up: container removed, unmounted, detached (Steps 7.1.9 to 7.1.11) | NOT CHECKED | |
| 7. The scratch volume is deleted at the end (Step 7.1.12), and the live data was not touched | NOT CHECKED | |
| 8. Time the rehearsal took | NOT CHECKED | |
| 9. A routine deploy by hand: `deployed.` or `already deployed` (Steps 2.2.1 to 2.2.4) | NOT CHECKED | |
| 10. The refusal digest of 64 zeros: exit 4 and its fixed text (Step 2.3.2) | NOT CHECKED | |
| 11. The refusal of the value `latest` (Step 2.3.1) | NOT CHECKED | |
| 12. The output matched section 3's table | NOT CHECKED | |
| 13. One rotation of the ingest key end to end, no value printed (section 6.2) | NOT CHECKED | |
| 14. The teardown steps were read against the README sections (Step 4) | NOT CHECKED | |
| 15. The runbook's `Expected:` lines replaced by what was seen | NOT CHECKED | |

## Risk

The rehearsal attaches and deletes a scratch volume, the deploy swaps the live app, and the rotation changes the live secret. The runbook's Risk lines give the undo for each. Nothing here deletes the live data volume, a snapshot, a secret, the state bucket or the image package.
