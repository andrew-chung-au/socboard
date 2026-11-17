# Land the on-call drill workflow, give it a model credential, run it once and keep the evidence

## Purpose

Run the drill that proves the On-Call Agent criterion: a planted defect makes `QueueLatencyOverSLO` fire, an agent reads the incident and opens a draft pull request with a fix and a test. Issue: #299. Rubric: On-Call Agent (and Alerting on User Impact). The workflow is `.github/workflows/oncall-drill.yml` (landed on `main`; workflows are protected files, so a person lands any change to it, from the copy in `observability/drill/proposed/oncall-drill.yml`).

The model credential is `CLAUDE_CODE_OAUTH_TOKEN`, made from a Claude subscription with `claude setup-token`, so no Anthropic API key is needed. This file never contains its value, and you never paste it into a comment, a file or a log.

## Where to run it

- In a terminal on your own machine, opened at the repository root of a clean clone of `main`, signed in with `gh auth status` as an owner of the repository. The `{owner}` and `{repo}` in the commands are filled in by `gh` from the clone. Commands run from the repository root. A shortcut such as `RUN` (set in step 4) lives only in the terminal that set it: in a new terminal, set it again.
- Steps marked "browser" are pages on github.com.
- Not here: the agent's sandbox. The agent never holds the credential and never changes repository settings.

## Before you start

1. Check that `gh` is signed in and `claude` is installed and signed in.

```
gh auth status
```

Expected: `Logged in to github.com` and no error.
If different: run `gh auth login`, then repeat.

```
claude --version
```

Expected: a version number.
If different: install Claude Code, run `claude` once and sign in with the account you normally use for headless runs.

2. Check that you are on an up-to-date `main`.

```
git switch main && git pull --ff-only
```

Expected: `Already up to date.` or a fast-forward, and no error.
If different: stop and tell the orchestrator what was printed.

## Steps

### Step 1: do the settings of #298 if you have not (`_human-tasks/2026-11-17T0307Z-human-task-298-engineer-oncall-environment-and-ruleset.md`)

Do its steps 1, 3, 4 and 5 (the `oncall` Environment limited to `main`, the ruleset on `main`, Actions may open pull requests, auto-merge off). Skip its step 2: this drill uses a different secret name, set in step 2 below.

Check each one now:

```
gh api repos/{owner}/{repo}/environments/oncall/deployment-branch-policies --jq '.branch_policies[].name'
```

Expected: exactly one line, `main`.
If different: do step 1 of the #298 file.

```
gh api repos/{owner}/{repo}/rulesets --jq '.[] | [.name, .enforcement, (.bypass_actors | length)] | @tsv'
```

Expected: a row `main`, `active`, `0`.
If different: do step 3 of the #298 file.

```
gh api repos/{owner}/{repo}/actions/permissions/workflow --jq '[.default_workflow_permissions, .can_approve_pull_request_reviews] | @tsv'
```

Expected: `read` and `true`.
If different: do step 4 of the #298 file.

### Step 2: make the credential and put it in the `oncall` Environment

Make a long-lived token for the drill. It opens a browser to sign in and then prints a token that starts with `sk-ant-oat`.

```
claude setup-token
```

Expected: a sign-in in the browser, then a token printed once. Copy it; it is not shown again.
If different: stop and tell the orchestrator what was printed, but not the token.

Store it. The next command asks for the value and does not show it. Paste the token, then press Enter. Do not put the value on the command line.

```
gh secret set CLAUDE_CODE_OAUTH_TOKEN --env oncall
```

Expected: `Set Actions secret CLAUDE_CODE_OAUTH_TOKEN for environment oncall`.
If different: stop and tell the orchestrator what was printed (not the token).

```
gh secret list --env oncall
```

Expected: a row for `CLAUDE_CODE_OAUTH_TOKEN`.
If different: no row means it was not saved; repeat the previous command.

Check the repository has no secret of that name outside the Environment, so only the agent job can read it:

```
gh secret list
```

Expected: no row named `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`.
If different: delete the stray one by its name, for example `gh secret delete ANTHROPIC_API_KEY`.

Risk: the token acts as your Claude account, and drill runs count against its usage limits. Undo: `gh secret delete CLAUDE_CODE_OAUTH_TOKEN --env oncall`, and revoke the token in your Claude account settings.

### Step 3: check the workflow and the two workflow changes are on `main`

They landed through pull requests #376, #377, #378 and #380 (the workflow, its patch and sandbox fixes, and the log fetch). A workflow run from a branch other than `main` cannot read the `oncall` Environment, so the file must be on `main` before step 4. Check that your `main` has it:

```
git switch main && git pull --ff-only
```

Expected: `Already up to date.` or a fast-forward, and no error.
If different: stop and tell the orchestrator what was printed.

```
grep -c "allow-escape-sequences" .github/workflows/oncall-drill.yml
```

Expected: `1`. That line is the last fix the workflow needed (the report job fetches the other jobs' logs with it).
If different: `0`, or `No such file`, means the workflow or its last fix is not on `main`; stop and tell the orchestrator.

The workflow changes are `ready_for_review` in the pull request types of `verify.yml` and `compose.yml` (so marking the agent's draft ready starts the normal CI), and the observability job's timeout of 30 minutes (the drift check runs the Python suite, about 6 minutes):

```
grep -c "ready_for_review" .github/workflows/verify.yml .github/workflows/compose.yml
```

Expected: two lines, each ending in `:1`.
If different: a `:0` means that workflow change is not on `main`; stop and tell the orchestrator.

### Step 4: run the drill once

Do not run it twice for evidence. Start it from `main`:

```
gh workflow run oncall-drill.yml --ref main
```

Expected: `Created workflow_dispatch event for oncall-drill.yml at main`.
If different: a 404 means the workflow is not on `main`; go back to step 3.

The run has a number, its **run id**. GitHub's tools call the same number `databaseId`, and it is also the last part of the run's web address (`.../actions/runs/` followed by the number). This command keeps it in the shortcut `RUN` for the rest of the steps:

```
RUN=$(gh run list --workflow oncall-drill.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

Expected: no output.
If different: an error means `gh` could not list the runs; check `gh auth status` and repeat.

```
echo "RUN=[$RUN]"
```

Expected: `RUN=[` then one number, for example `44065861050`, then `]`. Note the number now (the result table has a place for it).
If different: `RUN=[]` means the run has not started; wait a minute and repeat the two commands above.

The run takes about 25 to 35 minutes: the first job waits up to 10 minutes for the alert to fire.

The `agent` job waits for approval only if the `oncall` Environment has required reviewers; if it does, approve it in the browser (browser).

```
gh run watch "$RUN" --exit-status
```

Expected: all five jobs (`drill`, `agent`, `publish`, `check`, `report`) succeed and the command exits 0.
If different: an error that mentions a missing run means `RUN` is empty: set it again with the two commands above. Otherwise open the run's summary (browser). It says which stage stopped: no alert, hand-off failed, no change, guard refused, or a failed proof. Tell the orchestrator the run id and that line. Do not re-run before it is read.

### Step 5: read the result

```
gh api repos/{owner}/{repo}/pulls --jq '.[] | select(.head.ref | startswith("oncall/")) | [.number, .draft, .base.ref, .html_url] | @tsv'
```

Expected: one row for this run: a pull request number, `true`, `drill/` followed by the run id from step 4, and its link. An older drill's pull request may show too; read the row whose base ends in your run id.
If different: no row means no pull request was opened; read the run summary.

Open the pull request (browser), and read its merge box.

Expected: a draft whose base is `drill/` followed by the run id, and no auto-merge. Do not merge it.
If different: if it can merge into `main` without approval, or auto-merge is on, tell the orchestrator at once and close it.

This also answers the `VERIFY` of step 6 in the #298 file: does the merge box require a review? Write what it says in the table below.

### Step 6: keep the evidence

Download the incident file and the screenshots of the firing alert, then commit them with a record. This step needs `RUN` from step 4 (check with `echo "RUN=[$RUN]"`). The folder for the run's record is `_docs/evidence/observability/` plus a dated name; the first run's is `2026-11-17-oncall-drill`, which already holds its `README.md` and `manifest.json`.

```
gh run download "$RUN" --name drill-evidence --dir .scratch/drill-evidence
```

Expected: no error.
If different: a message that the name is not found means the `drill` job kept no evidence; say so in the record.

```
find .scratch/drill-evidence -type f
```

Expected: `incident.json`, `evidence/alert.png` and `evidence/dashboard.png`, each under `.scratch/drill-evidence`.
If different: a missing screenshot is not a failed drill; say so in the record.

Copy the three files up into the record's folder (the manifest check does not look inside subfolders):

```
cp .scratch/drill-evidence/incident.json .scratch/drill-evidence/evidence/alert.png .scratch/drill-evidence/evidence/dashboard.png _docs/evidence/observability/2026-11-17-oncall-drill/
```

Expected: no output.
If different: `No such file` names the one that is missing.

Add each file to `files` in `_docs/evidence/observability/2026-11-17-oncall-drill/manifest.json`, with what it shows (copy the entries already there), then check the folder. This is the check `make test` runs over every evidence folder:

```
uv run python observability/screenshots/manifest.py check _docs/evidence/observability/2026-11-17-oncall-drill
```

Expected: `observability manifest: ok`.
If different: it names each problem: a file in the folder but not in the manifest, a file listed but missing, or a canary string. A canary means that file must not be committed; stop and tell the orchestrator.

In `README.md` of that folder, write the date, the run id and link, the pull request link, what was checked (the alert's time in `incident.json`; the agent's pull request; the before and after of the new test from the `check` job's log; the log scan found nothing) and the result. Then commit all of it on a branch and open a pull request to `main`.

### Step 7: delete the throwaway branches

Do this after the evidence is kept. The run pushed `drill/` and `oncall/` branches named with its run id. Deleting `oncall/` closes the agent's draft pull request, whose page and diff stay readable.

```
git push origin --delete "drill/$RUN" "oncall/$RUN"
```

Expected: two lines, each starting with ` - [deleted]`.
If different: `remote ref does not exist` means it was already deleted; carry on. An empty `RUN` names `drill/` and fails: set it again with step 4's commands.

An earlier drill run's branches can go the same way: set `RUN` to that run's number (for example `RUN=44091359362`), run `echo "RUN=[$RUN]"` to see it, then repeat the delete.

## Record the result

Paste the output or write pass or fail for each step, with the date. Results contain no secrets.

| Step | Pass or fail | Output or note |
|---|---|---|
| Before you start | | |
| 1 | | |
| 2 | | |
| 3 | | |
| 4 (run id) | | |
| 5 (merge box says) | | |
| 6 | | |
| 7 | | |

## Risk

The run costs about 30 minutes of CI and some of the credential's usage. It pushes two throwaway branches, `drill/` and `oncall/` followed by the run id, and opens one draft pull request into the first. Delete both branches after you have kept the evidence (step 7); nothing in `main` changes. The token is readable only by the agent job; a leak is limited by the ruleset on `main` and by revoking it in your Claude account.
