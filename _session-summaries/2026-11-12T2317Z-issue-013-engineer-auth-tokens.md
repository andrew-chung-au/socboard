# Issue #13: Seeded minimal auth: login, bearer token and role checks

**Date:** 2026-11-12T23:17Z (UTC)
**Spec version:** 36d9ad7

Tier: Heavy (from the PM proposal). Engineer session, launched by the orchestrator; QA (two launches) has not run yet.

## What changed and why

Two Analysts and one Lead can now log in and get a bearer token, and every protected route knows who is calling and in what role (spec section 2). Tokens are HMAC-SHA256 signed `<payload>.<signature>` with a fixed 8-hour lifetime. Passwords are scrypt hashes (N=2^17, r=8, p=1). The role is read from the database on every request. `create_app()` requires `SOCBOARD_TOKEN_SECRET` and wires #11's engine with one session per request. `make seed` adds the three users from three password variables. The decision record is `_docs/decisions/2026-11-12T2255Z-auth-tokens.md`. Engineer comment: https://github.com/andrew-chung-au/socboard/issues/13#issuecomment-6838721775

**What I learned:** FastAPI 0.162 keeps an included router as one `_IncludedRouter` entry in `app.routes`; a route walk needs `fastapi.routing.iter_route_contexts`.

## Reflection

### Engineer

unknown (role not asked; reflection is collected after QA PASS)

## Launch usage

none (this session launched no subagents)

## Files created or modified

- New: `socboard/auth.py`, `socboard/passwords.py`, `socboard/tokens.py`, `socboard/schemas.py`, `socboard/sessions.py`, `tests/test_auth_api.py`, `tests/test_auth_config.py`, `tests/test_auth_passwords.py`, `tests/test_auth_seed.py`, `tests/test_auth_tokens.py`, `_docs/decisions/2026-11-12T2255Z-auth-tokens.md`
- Modified: `socboard/app.py`, `socboard/server.py`, `socboard/seed.py`, `socboard/errors.py`, `tests/backend_support.py`, `tests/conftest.py`, `tests/data_support.py`, `tests/test_data_audit.py`, `tests/test_inventory.py`, `README.md`, `_docs/index.md`
- Staged, not committed (waiting for approval): `AGENTS.md` (one clause on the Run line)
- Unchanged: `tests/test_backend_order.py`, `Makefile`, `pyproject.toml`, `uv.lock`

## Decisions made by the human

From before this session (issue comments): the 8-hour token lifetime; seed passwords from environment variables; `get_session` stays in #13; login attempt limiting is out of scope. Pending: approval of the staged `AGENTS.md` diff.

## Mismatches with the spec or design docs

none

## Commands to validate

- **Verify:** `make verify` (PASS with one warning: `tests/conftest.py` fixtures changed, as the issue requires)
- **Contract check:** `make contract-check` (`openapi.yaml: OK`)
- **Test (one file):** `make test-one FILE=tests/test_auth_api.py`

## Proposed AGENTS.md changes

1. Run line: "the backend needs `SOCBOARD_TOKEN_SECRET`, at least 32 characters, or exits 2" (staged; needs the human's approval).
2. Seed line: name the three users and the three password variables (proposal only; outside the issue's approved scope). The exact text is in the Engineer comment.

## Temporary changes

none. No server started, no system change, no install. Probe scripts in `.scratch/` (scrypt timing, route listing, surrogate login, dummy-hash generation, the comment file) are left for `make clean-scratch`.

## Interruptions

none

## Unrelated problems noticed but not fixed

- The `Makefile` `seed` help text describes only the asset inventory (the issue keeps `Makefile` untouched).

## Follow-ups for the next session

- The human approves or declines the staged `AGENTS.md` diff, and decides on the Seed-line proposal.
- First QA launch, then the second, independent QA launch (Heavy).
