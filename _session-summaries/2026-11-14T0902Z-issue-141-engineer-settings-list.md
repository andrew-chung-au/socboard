# Issue #141: Read all runtime settings from one list and refuse a missing or weak secret in deployed mode

**Date:** 2026-11-14T09:02Z (UTC)
**Spec version:** 36d9ad7

## What changed and why

`socboard/settings.py` lists every runtime variable (`SETTINGS`): name, secret, required in deployed mode, required by `make run`, and the rule. The names come from the modules that already read them, so none is written twice. `SOCBOARD_MODE` set to exactly `deployed` turns on deployed mode. Then `create_app` refuses a missing, blank, short or one-repeated-character ingest key, a missing or blank database URL, and a token secret of one repeated character, by raising `ConfigError`, so `python -m socboard.server` exits 2 and names only the variable. `scripts/run_preflight.py` takes `REQUIRED` from the list's `make_run` entries. The README has a variable table and a build-arguments line, and the example env file at the root lists names and rules. #140 had merged, so `SOCBOARD_STATIC_DIR` is in the list.

**What I learned:** three existing tests that must pass unchanged accept a one-repeated-character token secret outside deployed mode, so the "every mode" part of criterion 4 could not be done as written.

## Reflection

### Engineer

Not asked yet (the orchestrator asks after QA PASS).

## Launch usage

none

## Files created or modified

`socboard/settings.py` (new), `socboard/app.py`, `socboard/server.py` (docstring only), `scripts/run_preflight.py` (REQUIRED only), `tests/test_settings.py` (new), `README.md` (one section), the example env file (new), this summary.

## Decisions made by the human

none yet. The module name, `SOCBOARD_MODE` and its value `deployed` go to the human at the decision point, along with the criterion 4 conflict.

## Mismatches with the spec or design docs

Criterion 4 against the unchanged-tests rule (`tests/test_make_run.py`, `tests/test_auth_follow_ups.py`, `tests/test_auth_config.py` accept `"s" * 32` or `"y" * 32`). The repeated-character rule for the token secret applies in deployed mode only. This is recorded on the issue.

## Commands to validate

**Verify:** `make verify BASE=0737bb6` (local): PASS, 1876 Python and 208 frontend tests.

## Proposed AGENTS.md changes

Under Commands, Run: add "`SOCBOARD_MODE=deployed` turns on deployed mode (see README, Runtime variables); no make target sets it." Makefile, for a later sitting: `make run` and `make backend-run` could clear `SOCBOARD_MODE`, so a value inherited from the shell cannot switch a development run into deployed mode.

## Temporary changes

none

## Interruptions

none

## Unrelated problems noticed but not fixed

none

## Follow-ups for the next session

If the human wants the repeated-character rule for the token secret in every mode, the three test values above must change to mixed values. That change needs the human's approval.
