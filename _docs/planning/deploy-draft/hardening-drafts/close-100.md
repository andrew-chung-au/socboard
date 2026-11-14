Orchestrator: Close

Built by Session A (Kiro Web): the Engineer and QA launches are in the thread; closed out by the Hardening session. Standard tier. `.python-version` holds `3.12`, so uv picks 3.12 in CI and on a fresh checkout (CI had been on 3.14.4); `scripts/check_python_version.py` fails when the running major.minor differs from the file, and `.github/workflows/verify.yml` runs it after `make install`. `pyproject.toml`, `uv.lock` and the `Makefile` are unchanged. The workflow edit was committed with `HUMAN_APPROVED=1` after the human approved the exact diff.

Not done: the three Reviewer findings (https://github.com/andrew-chung-au/socboard/issues/100#issuecomment-6875049803) were not applied before the merge: the repository-file test fails a local run on a non-3.12 interpreter, and the dev container image `python:3.12` names the version a second time. Not verified: the tests locally on 3.12 (CI run 43578997610 is the evidence).

QA PASS commit: bba789d
Merge commit: 7388d86
Verify: CI runs 43578997610 (pull_request) and 43578993849 (push) on bba789d, make verify passed (`Using CPython 3.12.16`, 1847 Python tests, 208 frontend tests).
Summary: _session-summaries/2026-11-14T1306Z-issue-100-orchestrator-python-pin.md
