# Update the deploy document, put prod's secret and run the first prod deploy

## Purpose

Update the Systems Manager document `socboard-deploy` to its second parameter, `environment`, put the value of the prod secret `socboard/app-prod`, deploy the digest dev is running to the prod stack once, and check that prod answers, that dev did not change, that a repeat changes nothing, and that Systems Manager refuses a bad environment. Issue: #267. The script and the document change are merged first; merging deploys nothing to prod, so this is the first time the prod branch of the script runs for real.

## Where to run it

Your own machine, with your own AWS credentials for the account (SSO, region `us-east-1`), in a clone of the repository on `main` after the #267 pull request has merged. Steps that read the server use a Session Manager session and say so. Not here: the agent's sandbox, which has no Docker, Terraform or AWS access. The agent never runs `init`, `plan`, `apply` or a deploy.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

Values used below, never copied into this file: `<instance id>`, `<dev hostname>` and `<prod hostname>` are outputs of `terraform/server/`; `<digest>` is the digest dev is running, read in step 5; `<secret file>` is a path outside the repository, made in step 3; `<command id>` is printed by a send.

The destructive steps in this file: none. Step 14 (a reboot) stops both apps for the length of the boot, and it is your choice whether to run it; the file says so there.

## Before you start

1. Dev is deployed and healthy. Read the instance id first.

```
terraform -chdir=terraform/server output -raw instance_id
```

Expected: one instance id (`i-` and hex digits). Use it as `<instance id>`.
If different: stop and tell the orchestrator what was printed.

2. Read the dev hostname.

```
terraform -chdir=terraform/server output -raw hostname
```

Expected: `<ip-with-dashes>.sslip.io`. Use it as `<dev hostname>`.
If different: stop and tell the orchestrator.

3. Check that dev answers.

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<dev hostname>/api/health
```

Expected: `200`, with no certificate warning.
If different: stop. Dev is not healthy, so the before and after check in step 7 would prove nothing. Tell the orchestrator what was printed.

## Steps

### Step 1: read the prod hostname

```
terraform -chdir=terraform/server output -raw prod_hostname
```

Expected: `prod-<ip-with-dashes>.sslip.io`, the dev hostname with `prod-` in front. Use it as `<prod hostname>`.
If different: stop and tell the orchestrator what was printed.

### Step 2: check prod's data folder (from #266's apply)

Open a Session Manager session on the server.

```
aws ssm start-session --region us-east-1 --target <instance id>
```

Expected: a shell prompt on the server. The next three server commands run in it.
If different: stop and tell the orchestrator what was printed (a missing Session Manager plugin or a refused session names the problem).

```
sudo stat -c '%u %a %n' /srv/socboard-data/postgres-prod
```

Expected: `999 700 /srv/socboard-data/postgres-prod`.
If different: stop and tell the orchestrator. A prod run on a folder Docker created itself would leave it owned by root and the database would not start.

### Step 3: record dev's container before anything runs (still in the session)

```
sudo docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.Config.Image}}' socboard-app-1
```

Expected: one line: a container id, a start time and `ghcr.io/andrew-chung-au/socboard@sha256:` followed by 64 hex digits. Copy the whole line into the table below as the "before" value. The part of the image after `@` is `<digest>`, the digest to send in step 8: dev's own running digest, which is a digest that has already run in dev.
If different: `Error: No such object` means dev is not running; stop and tell the orchestrator.

```
exit
```

Expected: the session ends.

### Step 4: plan the document change

Read the plan before applying it. Run in `terraform/server/` with your credentials in the shell.

```
terraform -chdir=terraform/server plan
```

Expected: `Plan: 0 to add, 1 to change, 0 to destroy.` and the only resource listed is `aws_ssm_document.deploy`, updated in place (the `content` and nothing replaced; `# forces replacement` does not appear).
If different: stop, don't apply, and tell the orchestrator the list of resources. If the instance is listed, the plan is not this change.

### Step 5: apply the document change

```
terraform -chdir=terraform/server apply
```

Expected: `Apply complete! Resources: 0 added, 1 changed, 0 destroyed.`
If different: stop and tell the orchestrator what was printed (an access denied names the missing action).
Risk: updates one Systems Manager document in place. A deploy that is running during the apply keeps the script it started with. Undo: check out the earlier commit and apply again.

```
aws ssm describe-document --region us-east-1 --name socboard-deploy --query 'Document.Parameters[].[Name,DefaultValue]' --output text
```

Expected: two lines, `digest` first (its default shows as `None`) and `environment	dev`.
If different: stop and tell the orchestrator what was printed.

### Step 6: make prod's secret value on your own machine

First make the file, which a command creates empty with mode 600 in your temporary folder. This is the suggested default for `<secret file>`.

```
mktemp "${TMPDIR:-/tmp}/socboard-prod-secret.XXXXXX"
```

Expected: one path. Use it as `<secret file>`. Outside the repository, so it cannot be committed.
If different: stop and tell the orchestrator what was printed.

Then write the eight values into it. They are random, so they differ from dev's. The command prints nothing. The three seed passwords become the passwords of prod's three users; the `lead` one is what the smoke check logs in with.

```
python3 -c "import json, secrets, sys; names = ['SOCBOARD_TOKEN_SECRET', 'SOCBOARD_INGEST_API_KEY', 'SOCBOARD_SEED_PASSWORD_ANALYST_A', 'SOCBOARD_SEED_PASSWORD_ANALYST_B', 'SOCBOARD_SEED_PASSWORD_LEAD', 'STACK_PG_SUPERUSER_PASSWORD', 'STACK_PG_OWNER_PASSWORD', 'STACK_PG_APP_PASSWORD']; json.dump({n: secrets.token_hex(24) for n in names}, open(sys.argv[1], 'w'))" "<secret file>"
```

Expected: no output.
If different: stop and tell the orchestrator what was printed. Never print the file.

```
ls -l "<secret file>"
```

Expected: one line starting `-rw-------` and a size above 0.
If different: stop; the mode must be 600 before the next step.

### Step 7: put the value, and delete the file

```
aws secretsmanager put-secret-value --region us-east-1 --secret-id socboard/app-prod --secret-string file://<secret file>
```

Expected: a JSON reply with the secret's name and a version id, and no secret value.
If different: stop and tell the orchestrator what was printed (an access denied names the missing action).
Risk: sets prod's application secret (the passwords of prod's three users and of its database roles). A later value can be put the same way from a new file. Dev's secret `socboard/app` is a different secret and is not touched.

```
rm "<secret file>"
```

Expected: no output. The value now exists only in Secrets Manager.
If different: delete the file by hand before going on.

### Step 8: send the first prod deploy

The digest is the one dev is running (step 3), so no new image reaches prod unseen.

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest>,environment=prod --targets Key=tag:project,Values=socboard --query Command.CommandId --output text
```

Expected: one command id. Use it as `<command id>`.
Risk: runs the deploy for prod on the server: pulls the image and prod's database image, migrates and seeds prod's empty database, swaps prod's app. It touches nothing of dev's. The first prod deploy has no previous release to restore, so a failure after the swap leaves prod's app stopped and prod's database running; dev keeps answering.
If different: stop and tell the orchestrator what was printed (an error that the instance id may be wrong means the instance was replaced; read it again in the Before you start section).

```
aws ssm get-command-invocation --region us-east-1 --command-id <command id> --instance-id <instance id> --query '[Status,ResponseCode,StandardOutputContent]' --output text
```

Expected: `Success`, `0` and `socboard-deploy: deployed.` and nothing else (it can show `InProgress` for a minute; run it again).
If different: record the exit code and the output (only fixed lines and, after exits 5, 6, 7 and 9, a step name and up to 20 log lines), stop, and tell the orchestrator. The fix goes through a new issue, never an edit during the run. Exit 10 means the network's gateway is not `10.231.78.1`.

### Step 9: check prod from your machine

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<prod hostname>/api/health
```

Expected: `200`, with no certificate warning (the certificate is issued on the first request, so a first try can take a few seconds; run it once more before you call it a failure).
If different: record what was printed; stop.

### Step 10: check dev did not change

Open a Session Manager session again.

```
aws ssm start-session --region us-east-1 --target <instance id>
```

Expected: a shell prompt on the server.

```
sudo docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.Config.Image}}' socboard-app-1
```

Expected: the same line as the "before" value of step 3: the same container id, start time and image.
If different: dev's app was restarted or replaced by the prod run. Record both lines, stop, and tell the orchestrator.

```
sudo docker ps --format '{{.Names}}'
```

Expected: both `socboard-app-1` and `socboard-prod-app-1` in the list, and each stack's database container (`socboard-db-1`, `socboard-prod-db-1`).
If different: record what was printed; stop.

```
sudo ss -ltn
```

Expected: the two apps listen only on `127.0.0.1:8000` (dev) and `127.0.0.1:8100` (prod); no app port is on `0.0.0.0` or `*`.
If different: record the lines with those ports; stop.

```
exit
```

Expected: the session ends.

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<dev hostname>/api/health
```

Expected: `200`, as in the Before you start section.
If different: record what was printed; stop.

### Step 11: send the same digest to prod again

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest>,environment=prod --targets Key=tag:project,Values=socboard --query Command.CommandId --output text
```

Expected: one command id. Read the result as in step 8.

```
aws ssm get-command-invocation --region us-east-1 --command-id <command id> --instance-id <instance id> --query '[Status,ResponseCode,StandardOutputContent]' --output text
```

Expected: `Success`, `0` and `socboard-deploy: already deployed. Nothing was changed.`
If different: record the output; stop.

### Step 12: send a bad environment, twice

Refusal test: run these in your own machine's terminal, not in an agent session. The aim is to see that Systems Manager refuses a value that is not exactly `dev` or `prod`.
Risk: if the pattern did not hold, the script's own check refuses the value with exit 2 and the text `socboard-deploy: refused: the environment must be dev or prod. Nothing was changed.`; nothing is changed and no secret is printed either way.

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest>,environment=Prod --targets Key=tag:project,Values=socboard
```

Expected: an error from AWS that a parameter does not match its allowed pattern, and no command id.
If different: if a command id is returned, read its result as in step 8: exit 2 with the refusal text is the script's own check. Record both.

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest>,environment=staging --targets Key=tag:project,Values=socboard
```

Expected: the same error, and no command id.
If different: as above.

### Step 13: send with no environment

This is a dev run: the document's default is `dev`. Dev is running `<digest>`, so it says already deployed.

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest> --targets Key=tag:project,Values=socboard --query Command.CommandId --output text
```

Expected: one command id.

```
aws ssm get-command-invocation --region us-east-1 --command-id <command id> --instance-id <instance id> --query '[Status,ResponseCode,StandardOutputContent]' --output text
```

Expected: `Success`, `0` and `socboard-deploy: already deployed. Nothing was changed.`
If different: record the output; stop. A line `socboard-deploy: deployed.` here means dev was not running `<digest>`; a refusal means the default did not apply.

### Step 14: reboot the server (your choice; skip it and write SKIPPED in the table)

Pick a time: the reboot stops both apps for the length of the boot. The reboot is not needed for the rest of the file.

```
aws ec2 reboot-instances --region us-east-1 --instance-ids <instance id>
```

Expected: no output.
Risk: both apps are down until the stacks restart on their own (`restart: unless-stopped` on `db` and `app` of each).

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<prod hostname>/api/health
```

Expected: `200` within a few minutes (run it again until then, up to ten minutes).
If different: record what was printed after ten minutes; stop.

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<dev hostname>/api/health
```

Expected: `200`.
If different: record what was printed after ten minutes; stop.

## Record the result

Paste the output or write pass or fail for each row, with the date. Commit this file with the result so a later run can compare (`.kiro/steering/evidence-and-steps.md`, rule 2). Results contain no secrets. Dev's first run happened on 2026-11-16 (CI run 43979237337); prod's first run is the one this table records. After a teardown the hostnames written here are no longer live; this table, the CI runs and the pull request stay readable.

| Step | Pass or fail | Output or note |
|---|---|---|
| Before 1 to 3: dev is deployed and healthy (`200` on `<dev hostname>`) | NOT CHECKED | |
| 2: `stat` shows `/srv/socboard-data/postgres-prod` owned by 999 with mode 700 (from #266's apply) | NOT CHECKED | |
| 3: dev's app container id, start time and image, before (copy the line) | NOT CHECKED | |
| 4: the document's plan shows only `aws_ssm_document.deploy` changed in place (`0 to add, 1 to change, 0 to destroy`) | NOT CHECKED | |
| 5: apply of the document; `describe-document` lists `digest` and `environment` with default `dev` | NOT CHECKED | |
| 6 and 7: the prod secret's value made from a file on your own machine (random values, different from dev's), put into `socboard/app-prod`, the file deleted, no value printed | NOT CHECKED | |
| 8: the first send with dev's running digest and `environment=prod` prints `socboard-deploy: deployed.` | NOT CHECKED | |
| 9: `https://<prod hostname>/api/health` answers 200 with a valid certificate | NOT CHECKED | |
| 10: dev's app container id, start time and image are the same as before, and the dev URL still answers 200 | NOT CHECKED | |
| 10: `docker ps` shows `socboard-app-1` and `socboard-prod-app-1`; `ss -ltn` shows the apps only on 127.0.0.1:8000 and 127.0.0.1:8100 | NOT CHECKED | |
| 11: the same send again says `socboard-deploy: already deployed. Nothing was changed.` | NOT CHECKED | |
| 12: a send with `environment=Prod` and one with `environment=staging` are refused by Systems Manager and change nothing | NOT CHECKED | |
| 13: a send with no environment is a dev run and says already deployed | NOT CHECKED | |
| 14: after a reboot both stacks come back (or SKIPPED) | NOT CHECKED | |

## Risk

Step 8 deploys prod for the first time: a failure after the swap leaves prod's app stopped (there is no previous prod release), and dev keeps running. Step 7 sets the prod secret, which cannot be undone for a first value. Nothing here destroys data or touches dev's secret, data folder or containers. The reboot in step 14 is your choice.
