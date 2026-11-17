# Install the promote workflow, then promote to prod, roll back, and show the refused runs

## Purpose

Install `promote-prod.yml`, set its two Environment variables, promote dev's digest to prod, roll prod back to an earlier dev digest and forward again, and show three refused runs. This is the evidence for #269's criteria (rubric: CI/CD, the manual deploy to production; spec Phase 3, the rollback path). The Engineer has **not checked** any of it: no Actions, AWS or server was available. Every `Expected:` line is what the author expects. Issue: #269. Decision: `_docs/decisions/2026-11-16T0332Z-promote-to-prod.md` (proposed).

## Where to run it

The clean clone (repository root, on `main` after #269's pull request merged) for `git` and `gh`, and a browser for the Actions tab and the prod URL. Not here: the sandbox where the Engineer worked. Never paste a token, an ARN or the account number into this file; the steps print none.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

## Before you start

Each of these must be true; the step that needs it says so if it is not.

1. Dev has been deployed by CI at least once since #238's workflow was installed, so a `deploy-record-dev` artifact exists:

```
gh api "repos/andrew-chung-au/socboard/actions/artifacts?name=deploy-record-dev&per_page=5" --jq '[.artifacts[] | select(.expired == false)] | length'
```

Expected: `1` or more. For the rollback step you need two different dev digests, so ideally `2` or more from two different commits.
If different: `0` means nothing to promote. Merge a change to `main` and wait for the dev deploy, then start again.

2. The prod stack exists: #266 is applied (done 2026-11-16) and #267's first prod deploy by hand has run (its task file), so the server can run the document with `environment=prod`.

```
gh api repos/andrew-chung-au/socboard/issues/267 --jq .state
```

Expected: `closed`.
If different: stop; promote would reach a server without the `environment` parameter, and Systems Manager would refuse the send.

3. `git status --short` prints nothing and `git branch --show-current` prints `main`.

## Steps

### Step 1: Install the workflow (a protected file)

The install is a pull request (branch `issue-269-install-promote`, 2026-11-17). It moves the proposal to `.github/workflows/promote-prod.yml` unchanged and adds the workflow's exception to the id-token rule in `tests/test_terraform_plan_workflow.py`. Approving and merging that pull request is this step. It replaces the earlier copy-and-commit commands.

Check it after the merge:

```
gh api repos/andrew-chung-au/socboard/contents/.github/workflows/promote-prod.yml --jq .path
```

Expected: `.github/workflows/promote-prod.yml`. The Actions tab lists "socboard promote prod" with a "Run workflow" button.
If different: a 404 means the pull request is not merged yet.
Risk: adds a manual workflow. It does nothing until someone starts it and you approve it. Undo: delete the file in a new pull request.

### Step 2: Set the two Environment variables on `deploy-prod`

The instance is the same server as dev, so the suggested value is dev's:

```
gh api repos/andrew-chung-au/socboard/environments/deploy/variables/DEPLOY_INSTANCE_ID --jq .value
```

Expected: `i-` and hex digits (an instance id is not a secret).

If different: an HTTP 404 means `deploy` has no such variable yet (#238's steps set it); use `terraform -chdir=terraform/server output -raw instance_id` instead.

Set it on `deploy-prod` from the same place, so nothing is typed:

```
gh variable set DEPLOY_INSTANCE_ID --env deploy-prod --repo andrew-chung-au/socboard --body "$(gh api repos/andrew-chung-au/socboard/environments/deploy/variables/DEPLOY_INSTANCE_ID --jq .value)"
```

Expected: `✓ Created variable DEPLOY_INSTANCE_ID for andrew-chung-au/socboard` (or "Updated").
If different: an error naming the Environment means `deploy-prod` does not exist; it was created on 2026-11-16 (#268), so tell the orchestrator.

The URL is prod's hostname (#266's output `prod_hostname`); the suggested value:

```
terraform -chdir=terraform/server output -raw prod_hostname
```

Expected: `prod-44-216-250-242.sslip.io` (recorded at #266's apply, 2026-11-16) or the same form with your Elastic IP.
If different: an error means the server part's state is not reachable from this shell (credentials or `init`); use the `prod_hostname` value recorded in #266's result instead.

```
gh variable set DEPLOY_URL --env deploy-prod --repo andrew-chung-au/socboard --body "https://$(terraform -chdir=terraform/server output -raw prod_hostname)"
```

Expected: `✓ Created variable DEPLOY_URL for andrew-chung-au/socboard`.

```
gh variable list --env deploy-prod --repo andrew-chung-au/socboard
```

Expected: `DEPLOY_INSTANCE_ID` and `DEPLOY_URL`, and nothing else.
If different: fix the value with the same `gh variable set` command.
Risk: changes where prod deploys go. Undo: `gh variable delete <name> --env deploy-prod --repo andrew-chung-au/socboard`.

### Step 3: Promote dev's current digest

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main
```

```
gh run list --repo andrew-chung-au/socboard --workflow promote-prod.yml --limit 1 --json databaseId,url,status
```

Expected: one run. In the Actions tab, its `resolve` job passes and its summary shows "Chosen ... (dev is running it now)" and "Prod runs now". The `promote` job waits for review.

Approve it in the run's page ("Review deployments", tick `deploy-prod`, "Approve and deploy").

Expected: the `promote` job passes. Its log shows `socboard-deploy: deployed.` (or `already deployed`) and `health check 1 of 6: 200`. The summary table "Promote to prod" names the digest, "Started by" and "Approved by" with your login, and the run has an artifact `deploy-record-prod`. `VERIFY:` that "Approved by" shows your login (it reads the approvals endpoint; `not readable from GitHub` means it could not).
If different: a red `promote` job prints the script's fixed line or `ci_promote: ...` naming the problem. Copy that one line into the table and tell the orchestrator; do not re-run with a different digest. A send refused with "the instance id ... may be stale" means `DEPLOY_INSTANCE_ID` is wrong (step 2).

### Step 4: Check what prod serves

```
curl -sS -o /dev/null -w '%{http_code}\n' https://prod-44-216-250-242.sslip.io/api/health
```

Expected: `200`. Open the prod URL in a browser: the sign-in page loads with a valid certificate.
If different: anything but `200` (or a certificate warning) means prod is not serving: copy the code into the table and tell the orchestrator; prod still runs what the script restored if the swap failed (#237). Do not promote again to "fix" it.

### Step 5: Roll back to an earlier dev digest, then forward again

The suggested digest is the second-newest digest dev ran (the summary of step 3's `resolve` job lists the same ones under "Recent dev digests"). Download dev's second-newest record:

```
gh run download --repo andrew-chung-au/socboard --name deploy-record-dev --dir .scratch/dev-record-2 "$(gh api 'repos/andrew-chung-au/socboard/actions/artifacts?name=deploy-record-dev&per_page=2' --jq '.artifacts[1].workflow_run.id')"
```

```
jq -r .digest .scratch/dev-record-2/deploy-record-dev.json
```

Expected: one line `sha256:` and 64 hex digits, different from the digest step 3 promoted.
If different: the same digest as step 3 means dev's two newest records are the same release; take the next row of the summary's list instead. An error from `gh run download` means there is only one dev record: let a second push deploy to dev first, then come back.

Use that digest as the input:

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main -f digest=sha256:<the 64 digits printed above>
```

Approve it. Expected: the `resolve` summary says "(named by the input)", the `promote` summary shows "Prod ran before" with step 3's digest, and the job passes as in step 3.
If different: `no dev deploy record holds this digest` means a typing error; copy the digest again from the line above. A red `promote` job: as step 3.

Then forward again, with no input:

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main
```

Approve it. Expected: prod is back on dev's current digest; "Prod ran before" shows the older digest.
If different: as step 3. Then delete the download: `rm -r .scratch/dev-record-2`.

### Step 6: Refused runs

Refusal test: in the Actions tab and the clean clone. Risk: if a refusal does not happen, prod gets a digest dev never ran, or a run from another branch reaches the role. The steps print no secret either way.

**6a. A digest that never ran in dev** (suggested value: 64 zeros):

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main -f digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
```

Expected: the `resolve` job fails with `ci_promote: no dev deploy record holds this digest: only a digest that ran in dev can go to prod`; the `promote` job is skipped and **no approval is asked**.
If different: if GitHub asks you to review a deployment for this run, **reject it** (the refusal test failed: a digest dev never ran reached the approval). Then tell the orchestrator and treat promote as unsafe until it is fixed.

**6b. A dispatch from another branch:**

```
git switch -c promote-probe
```

Expected: `Switched to a new branch 'promote-probe'`.

```
git push -u origin promote-probe
```

Expected: the branch is created on `origin` (it holds the same commit as `main`, so the pre-push check has nothing new to verify).
If different: a refused push means the name exists already; pick `promote-probe-2` in this and the following commands.

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref promote-probe
```

Expected: both jobs are skipped (their `if` requires `main`); no approval is asked and nothing is deployed. (#268's refused-branch run already shows the role refuses a non-`main` branch in `deploy-prod`.) `VERIFY:` that a dispatch on another branch shows both jobs as skipped rather than failing.
If different: if GitHub asks you to review a deployment, **reject it** and tell the orchestrator: the main-only guard is missing. A `gh workflow run` error saying the workflow is not found on that ref is also a refusal; record it as a pass with that line.

```
git switch main
```

```
git push origin --delete promote-probe
```

```
git branch -D promote-probe
```

Expected: `Deleted branch promote-probe`, and `git ls-remote --heads origin promote-probe` prints nothing.

**6c. A rejected approval:**

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main
```

In the run page, choose "Reject". Expected: the `promote` job does not run, nothing is deployed, and no `deploy-record-prod` is added for this run. `VERIFY:` that a rejected review ends the job without running a step.
If different: if the `promote` job runs after a rejection, tell the orchestrator at once and check prod with step 4.

```
gh api "repos/andrew-chung-au/socboard/actions/artifacts?name=deploy-record-prod&per_page=1" --jq '.artifacts[0].workflow_run.id'
```

Expected: the run id of step 5's second run, not this one.
If different: this run's id means a record was written after a rejection; tell the orchestrator.

## Record the result

Fill the table once, when you run the steps, with the date, the line you saw, and the run link. Commit this file with the result. No token, ARN or account number.

| Step | Date | Pass or fail | Output or note | Run link |
|---|---|---|---|---|
| 1 install | | NOT CHECKED | | |
| 2 variables | | NOT CHECKED | | |
| 3 promote dev's digest (approved; summary names the approver) | | NOT CHECKED | | |
| 4 prod health 200, sign-in page loads | | NOT CHECKED | | |
| 5a rollback to an earlier digest | | NOT CHECKED | | |
| 5b forward again | | NOT CHECKED | | |
| 6a digest never in dev: refused, no approval asked | | NOT CHECKED | | |
| 6b another branch: skipped | | NOT CHECKED | | |
| 6c approval rejected: nothing deployed | | NOT CHECKED | | |

## Risk

Steps 3 and 5 change what prod runs; a rollback is step 5's second run. The deploy script keeps the previous release and restores it if the new one fails after the swap (#237). Dev and prod share one lock on the server, so a promote during a dev deploy fails with "another run is in progress" and is started again.
