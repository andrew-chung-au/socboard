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

```
cp _human-tasks/269-proposed/promote-prod.yml .github/workflows/promote-prod.yml
```

```
git rm -q _human-tasks/269-proposed/promote-prod.yml
```

```
git add .github/workflows/promote-prod.yml
```

The pre-commit hook refuses a commit that touches `.github/*` unless you approve it; you are that approval.

```
HUMAN_APPROVED=1 git commit -m "Install the promote-to-prod workflow (#269)"
```

Expected: one commit adding the workflow and removing the proposal. `make verify` then reads the real file (the tests read whichever of the two exists, never both).
If different: if the hook still refuses, read its message and tell the orchestrator; do not use `--no-verify`.

```
git push origin main
```

Expected: the push succeeds; the Actions tab lists "socboard promote prod" with a "Run workflow" button.
Risk: adds a manual workflow; it does nothing until someone starts it and you approve it. Undo: delete the file in a new commit.

### Step 2: Set the two Environment variables on `deploy-prod`

The instance is the same server as dev, so the suggested value is dev's:

```
gh api repos/andrew-chung-au/socboard/environments/deploy/variables/DEPLOY_INSTANCE_ID --jq .value
```

Expected: `i-` and hex digits (an instance id is not a secret).

```
gh variable set DEPLOY_INSTANCE_ID --env deploy-prod --repo andrew-chung-au/socboard --body i-071fe10ec49fbbbe5
```

Use the value the previous command printed if it differs from `i-071fe10ec49fbbbe5`.

The URL is prod's hostname (#266's output `prod_hostname`); the suggested value:

```
terraform -chdir=terraform/server output -raw prod_hostname
```

Expected: `prod-44-216-250-242.sslip.io` or the same form with your Elastic IP.

```
gh variable set DEPLOY_URL --env deploy-prod --repo andrew-chung-au/socboard --body https://prod-44-216-250-242.sslip.io
```

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

### Step 5: Roll back to an earlier dev digest, then forward again

In step 3's `resolve` summary, under "Recent dev digests", pick the second row's digest (an older commit). Then:

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main -f digest=<the older digest>
```

Approve it. Expected: the `resolve` summary says "(named by the input)", the `promote` summary shows "Prod ran before" with step 3's digest, and the job passes as in step 3.
If different: `no dev deploy record holds this digest` means a typing error; copy the digest again from the summary.

Then forward again, with no input:

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main
```

Approve it. Expected: prod is back on dev's current digest; "Prod ran before" shows the older digest.

### Step 6: Refused runs

Refusal test: in the Actions tab and the clean clone. Risk: if a refusal does not happen, prod gets a digest dev never ran, or a run from another branch reaches the role. The steps print no secret either way.

**6a. A digest that never ran in dev** (suggested value: 64 zeros):

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main -f digest=sha256:0000000000000000000000000000000000000000000000000000000000000000
```

Expected: the `resolve` job fails with `ci_promote: no dev deploy record holds this digest: only a digest that ran in dev can go to prod`; the `promote` job is skipped and **no approval is asked**.

**6b. A dispatch from another branch:**

```
git switch -c promote-probe
```

```
git push -u origin promote-probe
```

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref promote-probe
```

Expected: both jobs are skipped (their `if` requires `main`); no approval is asked and nothing is deployed. (#268's refused-branch run already shows the role refuses a non-`main` branch in `deploy-prod`.)

```
git switch main
```

```
git push origin --delete promote-probe
```

```
git branch -D promote-probe
```

**6c. A rejected approval:**

```
gh workflow run promote-prod.yml --repo andrew-chung-au/socboard --ref main
```

In the run page, choose "Reject". Expected: the `promote` job does not run, nothing is deployed, and no `deploy-record-prod` is added for this run.

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
