# Add the dev deploy workflow and the publish job's hand-over, set the two variables, and record the first run

## Purpose

Apply #238's two protected changes, set the two `deploy` Environment variables, and record the first real run. The changes are a new `.github/workflows/deploy-dev.yml` and two steps at the end of the publish job in `.github/workflows/publish.yml`. Issue: #238.

## Where to run it

- Steps 1 to 4: the clean clone, on the branch `issue-238-deploy-dev`.
- Steps 5 and 6: GitHub's settings and the Terraform output, with your own credentials.
- Steps 7 to 9: the Actions list, after the merge.

The agent never edits `.github/` and has no AWS access.

## Before you start

### Step 1: check that the order allows it

Apply and commit steps 2 to 4 at any time; they run nothing until they are on `main`. **Do not merge** the pull request until both of these are true:

- #237's first deploy has run, so the document `socboard-deploy` exists and the server runs the stack.
- Steps 5 and 6 are done.

A merge before that makes every push to `main` fail the deploy job, naming the missing variable. Nothing else changes, but the Actions list turns red.

```
gh api repos/andrew-chung-au/socboard/issues/237 --jq .state
```

Expected: you know whether #237's first deploy has run. The issue may still be open for its stage (b) re-check; what counts is the first run's result row.
If different: do steps 2 to 4 now and stop before the merge.

## Steps

### Step 2: check the branch and the proposal

```
git fetch origin
git checkout issue-238-deploy-dev
git merge origin/main
ls _human-tasks/238-proposed
```

Expected: `deploy-dev.yml` and `publish-workflow.diff`.
If different: stop and tell the orchestrator what was printed.

### Step 3: put the two files in place and read them

```
git mv _human-tasks/238-proposed/deploy-dev.yml .github/workflows/deploy-dev.yml
git apply --unidiff-zero _human-tasks/238-proposed/publish-workflow.diff
git diff -- .github/workflows/publish.yml
```

Expected: `publish.yml` gains two steps at the end of the `publish` job and nothing else.
- "Write the published image for the deploy workflow" writes `published-image.json` with `jq`.
- The pinned `actions/upload-artifact` step uploads it as `published-image`, kept 7 days, overwritten on a re-run.

`deploy-dev.yml` has two jobs:
- `find`: no Environment, `actions: read`.
- `deploy`: Environment `deploy`, `id-token: write`, the role from `secrets.AWS_DEPLOY_ROLE_ARN`.

If different: stop and tell the orchestrator what was printed.

### Step 4: remove the proposal, check, commit and push

```
git rm --quiet _human-tasks/238-proposed/publish-workflow.diff
git add .github/workflows/deploy-dev.yml .github/workflows/publish.yml
git status --short
make verify
```

Expected: `git status` shows `.github/workflows/deploy-dev.yml` added (renamed from the proposal), `publish.yml` modified and the diff deleted, nothing else. `make verify` passes; `tests/test_deploy_dev_workflow.py` now reads the real files.
If different: send the failing test's name.

```
HUMAN_APPROVED=1 git commit -m "Add the dev deploy workflow and the publish job's hand-over (#238)"
git push origin issue-238-deploy-dev
```

Expected: the commit hook accepts the protected files because of `HUMAN_APPROVED=1`, and the push prints `issue-238-deploy-dev -> issue-238-deploy-dev`.
If different: if the hook refuses, send its message; never use `--no-verify`.

Tell the orchestrator once it is pushed. CI on the pull request checks both files statically; neither workflow runs on a pull request.
Risk: none until the merge. `workflow_run` workflows run only from `main`.

### Step 5: read the instance id and the host name

After #237's first deploy, in your Terraform checkout of `terraform/server/`:

```
terraform output -raw instance_id
```

Expected: `i-` followed by 17 hex digits.

The host name is the one in the public URL that #237's first deploy reached with a valid certificate: its task file's result row for the URL. Use that row, not a value from memory.
If different: if Terraform has no output, run `terraform output` and send the names (not the values) it lists.
Risk: read-only.

### Step 6: set the two Environment variables

On GitHub, go to Settings, then Environments, then `deploy`, then Environment variables. These are variables, not secrets: neither holds a credential or the account number.

- `DEPLOY_INSTANCE_ID`: the value from step 5.
- `DEPLOY_URL`: `https://` followed by the host name, with no path and no trailing slash.

Leave the Environment as it is:
- no required reviewer (dev deploys by itself)
- branch rule `main` only
- the secret `AWS_DEPLOY_ROLE_ARN` unchanged

Expected: the `deploy` Environment lists both variables.
If different: if there is no `deploy` Environment, stop. It comes from #207, and the role refuses a job outside it.
Risk: a wrong value fails the next deploy job before any AWS call, naming the variable.
Undo: delete the variable.

**When the instance is replaced** (a change to the bootstrap or to `acme_ca` replaces it), repeat steps 5 and 6. Until then every deploy fails at the send step with "the instance id in DEPLOY_INSTANCE_ID may be stale".

### Step 7: merge, then watch the first run

Merge the pull request once steps 1, 5 and 6 are done. The merge is a push to `main`. It starts the usual checks, then `socboard publish` runs once per completed workflow, and each of those starts one `socboard deploy dev` run.

Expected:
- Most deploy runs end green at `find` with "this publish run published nothing".
- The run started by the publishing `socboard publish` run goes through `deploy` and prints `socboard-deploy: deployed.`
- The health check prints 200.
- The run has the artifact `deploy-record-dev`.

If different: send the `deploy` job's last lines. A FAIL is fixed through a new issue, never by editing the run.
Risk: this is a real deploy to dev, the same one #237's first run made by hand.

### Step 8: re-run the deploying run once

On that `socboard deploy dev` run, choose "Re-run all jobs".

Expected: the `deploy` job prints `socboard-deploy: already deployed. Nothing was changed.` and uploads a fresh `deploy-record-dev`.
If different: send the job's last lines.
Risk: none. The same digest changes nothing on the server.

### Step 9: show that a pull request does not deploy

Open any pull request, or use the next one. When its checks finish, `socboard publish` runs with the gate skipped, then `socboard deploy dev` runs with `find` skipped.

Expected: no `deploy` job, and no deployment listed on the `deploy` Environment for it.
If different: stop and tell the orchestrator.

## Record the result

| Row | Pass, fail or not settled | Output or note |
|---|---|---|
| The run is `socboard deploy dev`, started by a `socboard publish` run | | |
| `find` found `published-image` | | |
| The role was assumed | | |
| `send-command` accepted the instance id and the document (first live check of the deploy role's resource split, `VERIFY:` in #156) | | |
| The command ended `Success` with the deployed line | | |
| The health check passed | | |
| `deploy-record-dev` exists and holds the digest the publish run printed | | |
| The re-run shows "already deployed" | | |
| A later push whose publish run was skipped shows `find` green with nothing deployed | | |
| A pull request's run did not start the deploy job (link) | | |
| Time from the push to the URL serving the new digest | | |
| `get-command-invocation` returned `InvocationDoesNotExist` in the first seconds (yes or no) | | |

## Risk

The deploy job can send only the one document, with a digest, to the tagged instance, and read its result. The role allows nothing else (#156). A wrong variable fails before any AWS call. A failed deploy leaves the current release serving (#237) and writes no record.
