# Issue #139: Record the Deploy decisions (Terraform on AWS, Postgres audit rule, rate limits)

**Date:** 2026-11-14T02:31Z (UTC)
**Spec version:** 36d9ad7

## What changed and why

A new decision record, `_docs/decisions/2026-11-13T2156Z-deploy.md` (status `proposed`; only the human sets `approved`), and one `_docs/index.md` row. The record holds the seven Deploy decisions of 2026-11-12 (from the roadmap), the three new decisions in the issue's approved Decision record section (Postgres audit triggers with an application role that does not own the table; in-process limits with one worker; the amendment that moves every IAM resource and the budgets to the human-applied part), the region (`us-east-1`, the human's own account), accepted risks, the Postgres 16 pin, a never-hold table that separates what is enforced today from what is only planned, and a list of who settles each `VERIFY:` item. Later Deploy issues (#154, #155, #156, #162 so far) cite it.

Worked by Session A (Kiro Web) in a git worktree, draft PR #151, branch `issue-139-deploy-decision-record`. Heavy tier: seven Engineer rounds, and both QA launches passed at the end at `c2f53c7` (first launch https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6870230152, second launch https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6870226656). The rounds were: 1 (write the record), 2 to 4 (QA findings, mostly the never-hold table claiming checks that do not exist), 5 (restructure the table into enforced-today and planned columns), 6 (the first Reviewer report's six findings), 7 (restore the issue body's wording on the state-bucket region, which round 6 had overridden).

**What I learned:** a table that pairs every rule with "the check that enforces it" invites overclaiming when most checks are future work; splitting "enforced today" from "planned" ended a run of QA failures.

## Reflection

### Engineer

Rounds 1 to 5: unknown (role not asked). Round 6 (comment https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6870075023):

Reflection:
- Slowed me down: the search tool does not index `_docs/`, so I checked issue bodies and the record with the shell.
- Nearly went wrong: the "not filed yet" wording was in four places besides the header, so a single replace would have left contradictions.
- Proposed change: a `make` target that lists issue numbers by D-number title so the record's numbers can be checked in one command.

Round 7 (comment https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6870213412):

Reflection:
1. Slowed: `gh run view --log` returned Forbidden here, so I used the jobs REST endpoint and grepped a saved log; CI took about 5 minutes to start and finish.
2. Nearly wrong: none blocked; the risk was a second rewrite adding procedure again, so I kept the edit to the one bullet.
3. Proposed change: `_docs/agent-kit/procedures/verify.md` could name the REST jobs-log route for sandboxes where `gh run view` fails, so engineers skip the trial and error; measured by the count of failed `gh` calls per round.

### QA

unknown (role not asked)

### Orchestrator

The orchestrator applied a Reviewer finding (4) that overrode approved issue text; the second QA launch caught it. A Reviewer finding applied on a recommendation must be checked against the approved body first.

## Launch usage

Engineer: 7 launches. QA first launch: 6 launches (FAIL at `5aa3272`; PASS at `1ae2345`, `6baa1ab`, `7a4e2e6`, `270e69b` and `c2f53c7`; each after the first was a fresh re-check). QA second launch: 6 launches including one that declined by mistake and was relaunched (FAIL at `1ae2345`, FAIL at `6baa1ab`, PASS at `7a4e2e6`, FAIL at `270e69b`, PASS at `c2f53c7`). Reviewer: 2 launches (`7a4e2e6`, and the delta to `c2f53c7`). All were subagents; active time and tool uses were not recorded.

## Files created or modified

- `_docs/decisions/2026-11-13T2156Z-deploy.md` (new)
- `_docs/index.md` (one row)
- `_session-summaries/2026-11-14T0231Z-issue-139-orchestrator-deploy-decision-record.md` (this file)

## Decisions made by the human

- Approved the issue's Decision record section as written, including Postgres 16 (Orchestrator comment https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6865879093).
- Region: `us-east-1`, the human's own account (home region `ap-southeast-2`), no organisation-policy check (in the session, recorded in `_docs/planning/deploy-backlog-proposal.md`).
- After two second-launch FAILs, option 1 (restructure the never-hold table), and the stopping rule that a Medium-or-higher finding fails the Heavy second launch while Lows are reported (https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6869773094; the steering sentence is PR #161). An earlier comment that recorded this decision from a message meant for the other session was corrected (https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6867105416).
- Applying the first Reviewer report's six findings, under the human's blanket approval of the orchestrator's recommendations (https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6869999175), with finding 4 later corrected back to the body's wording.
- **Still open at the decision point:** the second Reviewer report (https://github.com/andrew-chung-au/socboard/issues/139#issuecomment-6870251377): finding 1 (decision 10 versus the snapshot role) needs a human decision because it changes an approved decision's wording; findings 2 and 3 and the smaller point are wording. The QA Lows at `270e69b` and `c2f53c7` are listed in those comments.

## Mismatches with the spec or design docs

- Spec section 5's secrets list omits the token signing secret (spec note A, pending the human); the record marks it as added.
- The issue body's region criterion says "account and organisation" in `ap-southeast-2`; the record follows the human's later clarification (own account).

## Commands to validate

- **Decisions:** `make decisions` (the record is listed as `proposed`)
- **Verify:** `make verify`

## Proposed AGENTS.md changes

none

## Temporary changes

A git worktree at `.scratch/wt/139` (git-ignored); removed when the issue is closed. No system changes, no processes, no installs.

## Interruptions

- One second-launch QA subagent declined, reading other agents' notes in its context as its own prior work; nothing ran, and it was relaunched with a clarifying line.
- One second-launch QA comment (at `c2f53c7`) says `make assert-clean` confirmed the clean tree; that QA did not run it. The orchestrator checked: the worktree was clean at `c2f53c7` and `make assert-clean` passed in the main checkout.

## Unrelated problems noticed but not fixed

- `make issue-close` checks only that a Close comment names a merge commit, not that it exists on `origin` (recorded in PR #157).
- `qa-engineer.md` says any problem fails the Heavy second launch; this repository's ruling differs (PR #161).

## Follow-ups for the next session

- The human decides the second Reviewer report's finding 1 (and labels 2, 3 and the smaller point); if anything is applied, QA re-checks it and this summary is updated.
- After the merge, the human sets the record's status to `approved` (only the human does), and the orchestrator posts the Close comment.
