---
inclusion: always
---

# Checking the deploy server (maintainer's ruling, 2026-11-16)

Agent sessions may check the deploy server, with limited rights: no Terraform, no secrets, no shell. These rules apply to every lane.

1. **What you can use.** (a) The public network: `curl` of the dev and prod URLs (`/api/health`, the certificate, the pages). (b) The `socboard-agent` role (#330), which can send only the Systems Manager document `socboard-inspect` with one check name from its fixed list (containers, logs, folders, health, disk, and the read-only Postgres checks) and read the result. Nothing else.
2. **What you never do.** No `AWS-RunShellScript`, Session Manager or `socboard-deploy`; no Terraform; no reading a secret. If the role is denied an action, stop and report the action name; never look for another way to do it.
3. **Need a check that is not on the list?** Propose it as a change to `socboard-inspect` in an issue (a fixed command, read-only, redacted). Never ask for a shell instead.
4. **Changes go through code.** A fix is an issue, a merge and a deploy (CI deploys dev; the maintainer approves prod). Never ask the maintainer to edit the host.
5. **Record what you checked, once.** A check that is evidence goes into the issue's human-task result table or the evidence timeline (`_docs/records/2026-11-16T0000Z-measure-planning-deploy-evidence-timeline.md`) with the date, the check name and the result, never a secret, an account number or an ARN (`evidence-and-steps.md`).
6. **Mind both environments.** Dev and prod share the host; use the `-prod` checks only when the issue is about prod.
