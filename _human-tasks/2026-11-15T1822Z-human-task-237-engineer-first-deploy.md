# Create the deploy document, set the application secret and run the first deploy

## Purpose

Create the Systems Manager document `socboard-deploy`, put the application secret's value, run the deploy script once with the newest published image and check that the public URL loads, that a second run changes nothing, that the two refusals work, that the stack comes back after a reboot and that no `proxy` container runs. Issue: #237 (stage (b); stage (a) is the repository work, merged first).

## Where to run it

Your own machine, with your own AWS credentials for the account (SSO, region `us-east-1`), in a clone of the repository on `main` after the stage (a) pull request has merged. Step 5 reads the server with a Session Manager session. Not here: the agent's sandbox, which has no Docker, Terraform or AWS access. The agent never runs the first deploy.

If a tool shows a permission prompt, reject one prompt at a time; rejecting ends the agent's turn, so tell it to continue.

Values used below, never copied into this file: `<instance id>` and `<hostname>` are outputs of `terraform/server/` (`instance_id`, `hostname`); `<digest>` is read in step 4; `<secret file>` is a path outside the repository.

## Before you start

1. Dependencies: #236 stage (b) is closed (its result file is committed and its peer-address row is filled), and the change that pins the stack network's gateway to `10.231.77.1` in `compose.yaml` is merged to `main`. Without the pin the script stops at exit 10, because Docker then gives the network another gateway. The secret `socboard/app` already exists (made empty by #207 stage (b)). Read the peer address from #236's result file and compare it with the address the script expects.

```
grep -n "PROXY_ADDR=" terraform/server/socboard-deploy.sh
```

Expected: one line, `PROXY_ADDR='10.231.77.1'`, and the same address in #236's result file. If different: stop and tell the orchestrator both addresses; the script's value is a change to the script and a new document version.

## Steps

### Step 1: create the document

Run in `terraform/server/` with your credentials in the shell. Read the plan first.

```
terraform plan
```

Expected: `Plan: 1 to add, 0 to change, 0 to destroy.` with only `aws_ssm_document.deploy` added, and the instance not replaced (its user data is unchanged).
If different: stop, don't apply, and tell the orchestrator what the plan shows.

```
terraform apply
```

Expected: `Apply complete! Resources: 1 added, 0 changed, 0 destroyed.`
If different: stop and tell the orchestrator what was printed (an access denied names the missing action).
Risk: creates one Systems Manager document in the account. Undo: `terraform destroy -target=aws_ssm_document.deploy`.

### Step 2: make the secret's value on your own machine

The file holds one JSON object with the eight keys, random values, mode 0600, outside the repository. This command writes it and prints nothing. The three seed passwords become the users' passwords; the `lead` one is what the smoke check logs in with.

```
umask 077 && python3 -c "import json, secrets, sys; names = ['SOCBOARD_TOKEN_SECRET', 'SOCBOARD_INGEST_API_KEY', 'SOCBOARD_SEED_PASSWORD_ANALYST_A', 'SOCBOARD_SEED_PASSWORD_ANALYST_B', 'SOCBOARD_SEED_PASSWORD_LEAD', 'STACK_PG_SUPERUSER_PASSWORD', 'STACK_PG_OWNER_PASSWORD', 'STACK_PG_APP_PASSWORD']; json.dump({n: secrets.token_hex(24) for n in names}, open(sys.argv[1], 'w'))" "<secret file>"
```

Expected: no output; the file exists with mode 600 (`ls -l "<secret file>"`).
If different: stop and tell the orchestrator what was printed. Never print the file.

### Step 3: put the value

```
aws secretsmanager put-secret-value --region us-east-1 --secret-id socboard/app --secret-string file://<secret file>
```

Expected: a JSON reply with the secret's name and a version id, and no secret value.
Risk: sets the application secret (the passwords of the three users and of the database roles). A first value cannot be undone; a later value can be put the same way from a new file.

```
rm "<secret file>"
```

Expected: no output. The value now exists only in Secrets Manager.

### Step 4: read the newest published digest

```
gh run list --workflow publish.yml --branch main --status success --limit 1
```

Expected: one run of `socboard publish`. Open it, read the digest in the job summary (`sha256:` and 64 hex digits) and use it as `<digest>` below.
If different: no run means no image is published yet; stop and tell the orchestrator.

### Step 5: send the document and read the result

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest> --targets Key=tag:project,Values=socboard
```

Expected: a reply with a `CommandId`. Use it below.
Risk: runs the deploy on the server: pulls the image, migrates the database, swaps the app (a short outage). A failed step leaves the current release serving, or restores it; the first deploy has none to restore, so a failure after the swap leaves the app stopped and the database running.

```
aws ssm get-command-invocation --region us-east-1 --command-id <command id> --instance-id <instance id>
```

Expected: `Status` is `Success` (it can show `InProgress` for a minute; run it again), `ResponseCode` is 0 and the output holds `socboard-deploy: deployed.` and nothing else.
If different: record the exit code and the output (only fixed lines and, after exits 6, 7 and 9, up to 20 log lines), stop, and tell the orchestrator. The fix goes through a new issue, never an edit during the run.

### Step 6: open the URL and check the health route from your machine

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<hostname>/api/health
```

Expected: `200`, with no certificate warning. Then open `https://<hostname>/` in a browser: the page loads and the certificate is valid.
If different: record what was printed and the browser's message; stop.

### Step 7: send the same digest again

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=<digest> --targets Key=tag:project,Values=socboard
```

Then read the result as in step 5.

Expected: `socboard-deploy: already deployed. Nothing was changed.` and exit code 0. Sign in as `lead` and check the number of cases is the same as after step 5: the second run adds none (no smoke check ran).
If different: record the output; stop.

### Step 8: send a bad digest

Refusal test: run this in your own machine's terminal, not in an agent session. The aim is to see that Systems Manager refuses a value that is not a digest.
Risk: if the pattern did not hold, the script's own check refuses the value with exit 2 and the text `socboard-deploy: refused: ...`; nothing is changed and no secret is printed either way.

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=latest --targets Key=tag:project,Values=socboard
```

Expected: an error from AWS that the parameter does not match the pattern, and no command id.
If different: if a command id is returned, read its result: exit 2 with the refusal text is the script's own check (record both).

### Step 9: send a digest of 64 zeros

Refusal test: your own machine's terminal. The aim is to see exit 4 for a well formed digest that names no image.
Risk: none to the running release; the script stops after the failed pull (exit 4) and the app keeps answering.

```
aws ssm send-command --region us-east-1 --document-name socboard-deploy --parameters digest=sha256:0000000000000000000000000000000000000000000000000000000000000000 --targets Key=tag:project,Values=socboard
```

Read the result as in step 5. Expected: exit code 4 and the text `socboard-deploy: the image or the Compose files could not be fetched, or are not this repository's. The current release was not changed.`; then the curl of step 6 still prints `200`.
If different: record the output; stop.

### Step 10: reboot the server and open the URL again

Pick a time: the reboot stops the app for the length of the boot.

```
aws ec2 reboot-instances --region us-east-1 --instance-ids <instance id>
```

Expected: no output.
Risk: the app is down until the stack restarts on its own (`restart: unless-stopped` on `db` and `app`).

```
aws ec2 wait instance-status-ok --region us-east-1 --instance-ids <instance id>
```

Expected: returns with no output once the instance reports running and its status checks pass.

```
curl -sS -o /dev/null -w '%{http_code}\n' https://<hostname>/api/health
```

Expected: `200` (it can take a minute after the status checks; retry for up to five minutes), and the page loads in the browser.
If different: record what was printed; a URL that does not load is a FAIL linked to a new issue.

### Step 11: check that no proxy container runs

Open a Session Manager session on the server with your own credentials.

```
aws ssm start-session --region us-east-1 --target <instance id>
```

In the session, run:

```
docker ps --filter label=com.docker.compose.service=proxy --format '{{.Names}}'
```

Expected: no output. Any name printed is a FAIL. (`VERIFY:` that your credentials may start a session; the deploy role may send only `socboard-deploy`.)

## Record the result

Write pass or fail for each step. Commit this file with the result so a later run can compare. Results contain no secrets and no real address. A FAIL says what was seen and that the fix goes through a new issue.

| Step | Pass or fail | Output or note |
|---|---|---|
| Before you start: peer address in #236's result file equals the script's, and the gateway pin is merged | NOT CHECKED | |
| 1: the plan shows the document only; apply succeeded | NOT CHECKED | |
| 2 and 3: the secret's value was put from a file and the file deleted | NOT CHECKED | |
| 5: first send, exit code and output | NOT CHECKED | |
| 5: the full smoke check passed (the run printed `deployed.`) | NOT CHECKED | |
| 6: the URL answers with a valid certificate | NOT CHECKED | |
| 6: `/api/health` answers 200 from your machine | NOT CHECKED | |
| 7: the second run says already deployed and the case count is unchanged | NOT CHECKED | |
| 8: the bad digest is refused | NOT CHECKED | |
| 9: the digest of 64 zeros exits 4 and the app still answers | NOT CHECKED | |
| 10: reboot, the stack comes back and the URL loads | NOT CHECKED | |
| 11: no `proxy` container runs | NOT CHECKED | |

## Risk

The first deploy changes the live server: it creates the document, sets the application secret and swaps the app. The first swap has no previous release, so a failure after it leaves the app stopped and the database running until a fixed deploy. The reboot stops the app for the length of the boot. Nothing here prints a secret.
