# Add prod access: the secret, two re-applies, the `deploy-prod` Environment and its refused-branch check

## Purpose

Let a job in the new GitHub Environment `deploy-prod` assume the existing deploy role, let the instance read the prod secret as well as the dev one, and show that a throwaway branch cannot reach the role through `deploy-prod`. The Engineer has **not checked** any of it: no Terraform, AWS or GitHub settings were available. Every `Expected:` line is what the author expects. Issue: #268. Decision: `_docs/decisions/2026-11-15T2328Z-prod-deploy-trust-and-secret.md` (proposed).

Dev is untouched: the Environment `deploy`, the secret `socboard/app` and the role's rights stay as they are.

## Where to run it

The clean clone (repository root, on `main` after this pull request merged) with your own AWS credentials for the `socboard` profile in your shell, and a browser for the GitHub Actions tab. Not here: the sandbox where the Engineer worked, which has no AWS access. Never paste a value from a step into a file in the repository or a chat: the steps below print no ARN, token or account number.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

## Before you start

1. The first apply of both parts has run (#207): `iam-and-budgets/` and `ci-access/` are applied, and the Environment `deploy` exists. If it has not, do not run steps 2 and 3 here: check step 1 first, then the first apply in `terraform/README.md` (it now includes the changes, so its plan counts stay 8 and 7, or 6 when the account's OIDC provider is reused), then continue at step 4.

```
git branch --show-current
```

Expected: `main`, and `git status --short` prints nothing.
If different: stop and switch to a clean `main`.

2. Already done by the maintainer on 2026-11-16 and recorded in the table below, not run again: the secret `socboard/app-prod` (created empty) and the Environment `deploy-prod`. Steps 1 and 4 only check them.

## Steps

### Step 1: The empty prod secret and its ARN in the variable file (done: the maintainer created the secret `socboard/app-prod` on 2026-11-16)

Nothing to create. Check that it is in place before step 2. The ARN holds the account number, so these commands print only the name, the stage list and a count. If the variable file has no `app_prod_secret_arn` line yet (the count below is `0`), add it without printing the ARN, using the secret's own ARN:

```
printf '\napp_prod_secret_arn = "%s"\n' "$(aws secretsmanager describe-secret --profile socboard --region us-east-1 --secret-id socboard/app-prod --query ARN --output text)" >> terraform/iam-and-budgets/terraform.tfvars
```

Read back:

```
aws secretsmanager describe-secret --profile socboard --region us-east-1 --secret-id socboard/app-prod --query Name --output text
```

Expected: `socboard/app-prod`.

```
aws secretsmanager describe-secret --profile socboard --region us-east-1 --secret-id socboard/app-prod --query VersionIdsToStages --output text
```

Expected: `None` (the secret has no value; its value is set by the prod deploy issue, not here).

```
grep -c '^app_prod_secret_arn' terraform/iam-and-budgets/terraform.tfvars
```

Expected: `1`.
If different: `0` means the line is missing (run the `printf` command above, then this count again); `2` or more means the line was appended twice, so delete the extra one by hand. A line with an empty value `""` is refused by `terraform plan` (the variable's validation), so nothing is applied. `git status --short` must not list the variable file.
Risk: the secret costs about 0.40 USD a month. Undo of the secret: `aws secretsmanager delete-secret --profile socboard --region us-east-1 --secret-id socboard/app-prod --recovery-window-in-days 7`, then delete the variable file's line.

### Step 2: Re-apply `iam-and-budgets/`, reading the plan first

```
terraform -chdir=terraform/iam-and-budgets plan -out=tfplan
```

Expected: `Plan: 0 to add, 1 to change, 0 to destroy.` The one change is `aws_iam_role_policy.read_app_secret will be updated in-place`, and its policy lists exactly two `Resource` ARNs, `socboard/app-…` and `socboard/app-prod-…`, with the one action `secretsmanager:GetSecretValue`. `VERIFY:` the change is in place.
If different: any other count, any `destroy` or `replace` line, or a wildcard in the resources means stop. Do not apply. Tell the orchestrator the plan's resource lines.

```
terraform -chdir=terraform/iam-and-budgets apply tfplan
```

Expected: `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.` and `instance_profile_name = "socboard-instance"`.
If different: read the error, do not retry another way, and tell the orchestrator what was printed.

### Step 3: Re-apply `ci-access/`, reading the plan first

```
terraform -chdir=terraform/ci-access plan -out=tfplan
```

Expected: `Plan: 0 to add, 1 to change, 0 to destroy.` The one change is `aws_iam_role.deploy will be updated in-place`, in `assume_role_policy` only. Read the trust: the audience is `sts.amazonaws.com` and `sub` has exactly two values, `…:environment:deploy` and `…:environment:deploy-prod`, each starting `repo:andrew-chung-au@<owner id>/socboard@<repo id>`. The plan and apply roles and every role policy show no change.
If different: a third subject, a `*`, a subject without the ids, a change on another resource, or a replace of the role means stop. Do not apply. Tell the orchestrator.

```
terraform -chdir=terraform/ci-access apply tfplan
```

Expected: `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.` and the three role ARN outputs, unchanged.
If different: as step 2.

### Step 4: The `deploy-prod` Environment, read back (done: the maintainer created it on 2026-11-16)

Nothing to create. The Environment has you as the required reviewer, administrators not exempt, "prevent self-review" off (with one reviewer, a job could otherwise never be approved), and one deployment branch rule, `main`. The field `can_admins_bypass` is verified as the GitHub field name (2026-11-16, maintainer: set with `PUT /repos/{owner}/{repo}/environments/{name}`; `deploy` read back `false` after the PUT, `infra` reads `false`). This read-back is the check; run it again before step 6.

```
gh api repos/andrew-chung-au/socboard/environments/deploy-prod --jq '[.can_admins_bypass, (.protection_rules[] | select(.type == "required_reviewers") | .reviewers[0].reviewer.login), .deployment_branch_policy.custom_branch_policies]'
```

Expected: `[false,"andrew-chung-au",true]`.
If different: `true` first means administrators are exempt: set the box off in Settings, Environments, `deploy-prod`, and tell the orchestrator. No login second means no required reviewer; `false` last means the branch rule is not a custom policy. Fix it in the same settings page, or recreate it with `gh api -X PUT repos/andrew-chung-au/socboard/environments/deploy-prod` and the fields above, then add the branch rule with `gh api -X POST .../deployment-branch-policies -f name=main -f type=branch`.

```
gh api repos/andrew-chung-au/socboard/environments/deploy-prod/deployment-branch-policies --jq '[.branch_policies[].name]'
```

Expected: `["main"]` and nothing else.
Risk: changes who may deploy to prod. Undo: `gh api -X DELETE repos/andrew-chung-au/socboard/environments/deploy-prod`.

### Step 5: The role's unique ID is registered so GitHub masks it (done: the maintainer set `AWS_DEPLOY_ROLE_ID` on 2026-11-16)

Nothing to set now. The `aws iam get-role` command below is kept for a recreated role; the list command after it is the read-back to run now.

The credentials action prints the role's unique ID (it starts `AROA`) in clear, and GitHub masks only registered values. `deploy-prod` uses the same role as `deploy`, so one repository secret, visible to both Environments' jobs, covers both. A job masks it only if its workflow references the secret before the credentials step; that edit is the Compose and CI lane's.

```
aws iam get-role --profile socboard --role-name socboard-ci-deploy --query Role.RoleId --output text | gh secret set AWS_DEPLOY_ROLE_ID --repo andrew-chung-au/socboard
```

Expected: `Set Actions secret AWS_DEPLOY_ROLE_ID for andrew-chung-au/socboard`, and the ID itself is not printed.
If different: an AWS error means the role name or profile is wrong; do not type the ID by hand into a file or the shell history.

```
gh secret list --repo andrew-chung-au/socboard --json name --jq '[.[].name | select(startswith("AWS_"))]'
```

Expected: a list that includes `"AWS_DEPLOY_ROLE_ID"`.
Note: if the role is ever recreated, run this step again.

### Step 6: The refused-branch check for `deploy-prod`

Refusal test: use the clean clone and the GitHub Actions tab. Risk: if the Environment's branch rule is missing or wrong, the job prints `assumed socboard-ci-deploy/probe-deploy-prod`. It prints no key and no ARN, but it shows a non-`main` branch holds the deploy role through `deploy-prod`: stop, fix the rule, and treat `deploy-prod` as open until it refuses. The probe uses the existing secret `AWS_DEPLOY_ROLE_ARN`.

Create the branch:

```
git switch -c oidc-probe-deploy-prod
```

Expected: `Switched to a new branch 'oidc-probe-deploy-prod'`.

Create `.github/workflows/oidc-probe-deploy-prod.yml` with this content (it lives only on this throwaway branch, never on `main`):

```yaml
name: oidc-probe-deploy-prod
on:
  push:
    branches: ["oidc-probe-deploy-prod"]
permissions:
  id-token: write
  contents: read
jobs:
  probe:
    runs-on: ubuntu-latest
    environment: deploy-prod
    steps:
      - name: Try to assume the deploy role
        env:
          ROLE_ARN: ${{ secrets.AWS_DEPLOY_ROLE_ARN }}
        run: |
          TOKEN=$(curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=sts.amazonaws.com" | jq -r .value)
          echo "::add-mask::$TOKEN"
          if OUT=$(aws sts assume-role-with-web-identity --role-arn "$ROLE_ARN" --role-session-name probe-deploy-prod --web-identity-token "$TOKEN" --query 'AssumedRoleUser.Arn' --output text 2>&1); then
            echo "assumed ${OUT#*assumed-role/}"
          else
            C=$(grep -o -E '\([A-Za-z.]+\)' <<< "$OUT" | head -n 1 | tr -d '()'); echo "refused: ${C:-OTHER}"; exit 1
          fi
```

```
git add .github/workflows/oidc-probe-deploy-prod.yml
```

```
git commit -m "Probe: the deploy role from a throwaway branch (#268)"
```

```
git push -u origin oidc-probe-deploy-prod
```

Then read the run:

```
gh run list --repo andrew-chung-au/socboard --branch oidc-probe-deploy-prod --json databaseId,conclusion,status,url --jq '.[0]'
```

Expected: the run does not succeed. Either the job never starts and GitHub says the branch is not allowed to deploy to `deploy-prod`, or it starts and prints `refused: AccessDenied`. No `assumed` line is printed, and no approval prompt appears. Record which one, and the run URL, in the table.
If different: `assumed socboard-ci-deploy/probe-deploy-prod` means the refusal test failed (see Risk).

**Positive control** (without it, a refusal might come from a wrong trust and not from the Environment's rule). Allow the throwaway branch for a few minutes. Risk: while it is allowed, this branch can assume the role after you approve the job. Undo: the delete command below.

```
gh api -X POST repos/andrew-chung-au/socboard/environments/deploy-prod/deployment-branch-policies -f name=oidc-probe-deploy-prod -f type=branch --jq '.id'
```

Expected: a number (the policy id; note it).

```
gh run rerun --repo andrew-chung-au/socboard <run id from the run URL>
```

In the Actions tab, approve the waiting job as the reviewer.
Expected: the job passes and prints `assumed socboard-ci-deploy/probe-deploy-prod`. Record the run URL.
If different: `refused: AccessDenied` means the trust does not accept the `deploy-prod` subject. Do not loosen it. Read `terraform/README.md`, "If the subject has no ids", check step 3's plan, and tell the orchestrator the error code.

Remove the allowance and re-run:

```
gh api -X DELETE repos/andrew-chung-au/socboard/environments/deploy-prod/deployment-branch-policies/<policy id>
```

```
gh run rerun --repo andrew-chung-au/socboard <run id>
```

Expected: refused again, as in the first run. Record the run URL.

```
gh api repos/andrew-chung-au/socboard/environments/deploy-prod/deployment-branch-policies --jq '[.branch_policies[].name]'
```

Expected: `["main"]`.

Clean up (keep the secrets; the runs stay in the Actions history, which is the evidence; `VERIFY:` that a run's page and log stay readable after its branch and workflow file are deleted, and if they do not, paste the log's `refused` and `assumed` lines into the table):

```
git switch main
```

```
git push origin --delete oidc-probe-deploy-prod
```

```
git branch -D oidc-probe-deploy-prod
```

Expected: the branch is gone from `origin`.

## Record the result

Fill the table once, when you run the steps, with the date, what you saw, and the run links. Commit this file with the result. Results contain no secret, no ARN and no account number: paste the line the tool printed, nothing around it. Then add the two run URLs to the "Evidence" lines in `terraform/README.md` (the sections "CI access" and "The instance's IAM role, the snapshot schedule and the budgets").

| Step | Date | Pass or fail | Output or note | Link |
|---|---|---|---|---|
| 1 secret and variable | 2026-11-16 | DONE: secret `socboard/app-prod` created empty (maintainer). Variable-file line and read-backs: NOT CHECKED | | |
| 2 `iam-and-budgets/` plan and apply | | NOT CHECKED | | |
| 3 `ci-access/` plan and apply | | NOT CHECKED | | |
| 4 Environment and read-back | 2026-11-16 | DONE: Environment `deploy-prod` created (maintainer); `can_admins_bypass` verified as the field name. The read-back of this step: NOT CHECKED | | |
| 5 `AWS_DEPLOY_ROLE_ID` | 2026-11-16 | DONE: secret set (maintainer). The read-back of this step: NOT CHECKED | | |
| 6a refused run (throwaway branch) | | NOT CHECKED | | |
| 6b control run (branch allowed, approved) | | NOT CHECKED | | |
| 6c refused again (allowance removed) | | NOT CHECKED | | |

## Risk

The probe job runs with `id-token: write`, so a run that succeeds holds short-lived credentials for the deploy role during the job only. That role can send one Systems Manager document to the tagged server and cannot read a secret. Apart from the control in step 6, nothing here widens a permission, and the control is removed in the same step.
