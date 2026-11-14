# Postgres lane: groomed drafts for D5b, D6a, D6b and D7 (not filed)

Written 2026-11-14 by the Postgres lane session against `main` at `4c824e1`, from `_docs/planning/deploy-draft/wave-2-issue-drafts.md` (D6a, D6b) and the outlines in `_docs/planning/deploy-backlog-proposal.md` (D5b, D7). **Drafts only: nothing here is filed, groomed on an issue, or approved.** The human files and labels them. Choices made by the session, open to change: the 8-claim and 50-round numbers in D7, and the marker rule in D5b.

Facts read from the code: `tests/conftest.py` builds one migrated SQLite template and copies the file per test; `tests/data_support.py` and `tests/backend_support.py` each build SQLite URLs themselves, and 37 test modules import from them. The claim is one conditional UPDATE (`socboard/cases.py`), and `tests/test_claim_api.py` already has a thread-barrier test on SQLite. `socboard/audit_guard.py` is SQLite-specific (`sqlite_sequence`, the pragma words, `ATTACH`, a compile step that falls back to the SQLite dialect). #107 is open and not approved.

---

## D5b

Title: Run the backend tests on Postgres in CI: fixtures take the database from the URL

### Why

D5a (#143) gets migrations, seed and a smoke check onto Postgres. The test modules that build a database do it through `tests/conftest.py`, `tests/data_support.py` and `tests/backend_support.py`, each of which builds an SQLite file URL itself, so no test can run on Postgres yet; D6a and D7 need one.

### Goal

The backend tests run on SQLite by default and on Postgres 16 in CI, from the same test code, with the database chosen only by a URL.

### Acceptance criteria
- [ ] One helper owns "build a migrated test database and return its URL"; every fixture and test module uses it. No test builds `sqlite:///` text itself, except tests that are SQLite-only by nature.
- [ ] A variable (name chosen here; `VERIFY:` against D5a's names) selects Postgres. Unset, the whole default run uses SQLite exactly as today, with the same pass count.
- [ ] On Postgres each test gets an isolated database (a fresh schema or database per test, or a template copy); tests never share rows, and a failing test leaves nothing for the next.
- [ ] A CI job runs the Python tests against a Postgres 16 service container (a protected `.github/*` change: the Engineer returns the exact diff to the human).
- [ ] Every test that cannot run on Postgres (the trigger tests, the guard tests, SQLite-file tests) carries one visible marker with a one-line reason. A marked test is skipped only when Postgres is selected, and the run prints the marker count and the skip count. No silent skips: `make verify` reports no skip without a marker.
- [ ] Failures that remain on Postgres after the markers are listed in the PR body by test name with their owner issue (D6a for the triggers, D6b for the guard); the number of Postgres-run tests is stated.
- [ ] Postgres timestamps and ids behave as the SQLite tests expect, or the difference is recorded (`test_data_timestamps.py`, `test_data_ids.py`).
- [ ] `make verify` passes on SQLite with no new skips.

### Out of scope
- The Postgres triggers (D6a), the guard review (D6b), single ownership (D7).
- Application code changes beyond what a test needs to find the URL.

### Constraints
- Files: `tests/` (the three support files and the modules that build their own URLs); the CI job under `.github/workflows/` (protected).
- Depends on: D5a (#143) merged. The default test run must still pass on SQLite with no Postgres.
- Proposed tier: Standard.

---

## D6a

Title: Make the audit trail append-only on Postgres: triggers and tests

The wave-2 draft's text (`wave-2-issue-drafts.md`, D6a), with these edits:
1. The dependency on D0 is removed (CI is the evidence; D0 is merged as PR #179 for later local tests).
2. The tests reuse the D5b marker and run in the D5b CI job.
3. Roles are explicit: the CI Postgres setup creates the owner role (migrations) and a non-owner, non-superuser application role. `VERIFY:` that on Postgres 16 `ALTER TABLE ... DISABLE TRIGGER` needs the table owner or a superuser, so the test expects a "must be owner" error.
4. New criterion: a test shows the application role cannot set `session_replication_role` to `replica` (`VERIFY:` against the Postgres documentation; record decision 8).
5. Design settled: the triggers go in the Postgres branch of migration `0001`, with the SQLite output unchanged (a test compares it).

Criteria in short: UPDATE, DELETE and a duplicate-id INSERT refused with fixed messages (one test each); `TRUNCATE audit_entries`, alone or with other tables, refused by a statement trigger; `append_audit` still appends with ascending ids; the application role cannot disable triggers or run DDL and migrations run as the owner; the `session_replication_role` test; the current guard lets a normal insert through on Postgres; SQLite triggers and tests unchanged; one line in the data-model record on what the owner and a superuser can still do; `make verify` passes and the Postgres tests run in CI.

Proposed tier: Heavy (security boundary). Depends on: D5a and D5b merged.

---

## D6b

Title: Review the audit guard for Postgres after its simplification

The wave-2 draft's text (`wave-2-issue-drafts.md`, D6b), with these edits:
1. The SQLite-only parts are named from today's file: `sqlite_sequence`, the pragma words, `ATTACH`/`DETACH`, and the `sqlite.dialect()` fallback.
2. #107 is open and unlabelled; if it is not approved when D6a merges, this issue runs against the current guard (the human's ruling of 2026-11-13).
3. Postgres statements to test, one test each, saying whether the guard or D6a's database refusal stops it: `ALTER TABLE audit_entries DISABLE TRIGGER ALL`, `DROP TRIGGER`, `DROP TABLE`, `TRUNCATE`, `ALTER TABLE ... ENABLE/DISABLE RULE`.

Proposed tier: Heavy. Depends on: D6a merged and #107. Before D20.

---

## D7

Title: Prove single ownership on Postgres under simultaneous claims

### Why

Spec §5: a case has exactly one owner under simultaneous claims, on any supported database. The claim in `socboard/cases.py` is one conditional UPDATE, and the existing barrier test runs on SQLite only. Postgres concurrency differs (row locks, no database-wide write lock), so SQLite results prove nothing for it.

### Goal

Simultaneous claims of one case on Postgres leave exactly one owner and one `claimed` audit entry, and the losers get the contract's 409.

### Acceptance criteria
- [ ] With real separate connections, N (at least 8) claim requests for one case released together all return; exactly one gets 200 and the rest get 409 (`ALREADY_CLAIMED`) with the contract's error body.
- [ ] After the race the case has one owner (the winner) and exactly one `claimed` audit entry.
- [ ] A loser's response leaves the winner's state unchanged (owner, status and audit count re-read).
- [ ] The test repeats (at least 50 rounds, each on a fresh case) and fails if any round shows two owners or two entries.
- [ ] A claim racing a close of the same case leaves a consistent state (owned or closed as the contract allows, never a claimed audit entry on a case the close won).
- [ ] The same test passes on SQLite, or carries the D5b marker with a reason.
- [ ] A real race found by the test is reported with the failing output and not fixed silently; the fix is a separate decision.
- [ ] `make verify` passes; the Postgres run is in CI.

### Out of scope
- Changing the claim logic unless the test proves a race (then ask the human).
- Load or latency measurement.

### Constraints
- Files: `tests/` only; `socboard/cases.py` only if the test finds a race and the human approves.
- Depends on: D5a and D5b merged.
- Proposed tier: Standard.
