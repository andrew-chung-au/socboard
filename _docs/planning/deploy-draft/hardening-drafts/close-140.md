Orchestrator: Close

Built by Session A and B (Kiro Web): the Engineer, QA and Reviewer launches are in the thread; closed out by the Hardening session. Heavy tier. `socboard/spa.py` adds one GET and HEAD fallback route after every API router: it never matches `/api`, returns a file only if its real path is inside the folder, returns `index.html` for extension-less paths and a 404 for a missing path with an extension. The folder comes from `SOCBOARD_STATIC_DIR` (default `frontend/dist` in the repository root). One QA FAIL (M1, the existing tests would get the entry page where `frontend/dist` exists) was fixed with an autouse fixture in `tests/conftest.py`; the Reviewer's findings were applied in part at `2d030c5`; the second Heavy QA launch passed at `35cad54`.

Not done: Reviewer findings 1, 3, 4 and 5 and the QA Lows (dotfiles served from the folder, a folder rebuilt while running, `scope["path"]` with a `root_path`, test gaps) are left to the follow-up issue 160 by the human's decision of 2026-11-14. Not verified: the tests locally (CI is the evidence), Windows or a case-insensitive filesystem, a browser.

QA PASS commit: 35cad54
Merge commit: efbf67c
Verify: CI runs 43563650675 and 43563646167 on 35cad54, make verify passed (1837 Python tests, 208 frontend tests); `make assert-clean` passed at 2d030c5.
Summary: _session-summaries/2026-11-13T2203Z-issue-140-engineer-serve-built-frontend.md
