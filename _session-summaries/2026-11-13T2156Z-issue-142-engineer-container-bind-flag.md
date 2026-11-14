# Issue #142: Let the container image bind a non-loopback address through an explicit opt-in

**Date:** 2026-11-13T21:56Z (UTC)
**Spec version:** 36d9ad7

## What changed and why

`python -m socboard.server` now accepts one flag, `--allow-non-loopback-host`, that lets `SOCBOARD_HOST` be an IP address. No environment variable can enable it, so `make backend-run` and `scripts/backend_probe.py` (which call `resolve_bind(os.environ)` with the opt-in off by default) stay loopback-only with no Makefile change. A value given to the flag, an abbreviation or an unknown argument exits 2 with a usage line and starts nothing. Added tests, a README paragraph, a proposed decision record and its index row.

**What I learned:** `main` must default `argv` to empty, not `sys.argv`, because existing tests call `main(env)` under pytest; only the `__main__` block passes the real arguments.

## Reflection

Not asked yet.

## Launch usage

none

## Files created or modified

- `socboard/server.py`, `tests/test_backend_server.py`, `README.md`
- `_docs/decisions/2026-11-13T2156Z-container-bind-flag.md` (Status: proposed), one row in `_docs/index.md`
- This file.

## Decisions made by the human

The Decision record in the issue body was approved with the issue (flag, not an environment variable).

## Mismatches with the spec or design docs

none.

## Commands to validate

- **Verify:** `make verify` (CI on the draft pull request; Python tests could not run in the Kiro Web sandbox).

## Proposed AGENTS.md changes

none. The Run line already says `make backend-run` is localhost only, which stays true.

## Temporary changes

none. No server was started. Scratch files in `.scratch/` are git-ignored.

## Interruptions

none.

## Unrelated problems noticed but not fixed

none.

## Follow-ups for the next session

1. D10 (the Dockerfile) must pass `--allow-non-loopback-host` in its start command.
