"""`make e2e` (#234): the target refuses without a stack address, names the missing install, and is listed in `make help`.

The set case is shown by CI's Compose job, which runs the target against the stack.
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIABLE = "SOCBOARD_BASE_URL"


def run(*arguments, **environ):
    env = {key: value for key, value in os.environ.items() if key != VARIABLE}
    env.update(environ)
    return subprocess.run(["make", *arguments], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def assert_refused(done):
    shown = done.stdout + done.stderr
    assert done.returncode == 2, shown
    assert VARIABLE in shown
    # Nothing ran: Playwright prints neither its header nor a result line.
    assert "Running " not in shown and "passed" not in shown and "playwright test" not in shown


def test_the_target_refuses_when_the_address_is_unset():
    assert_refused(run("e2e"))


def test_the_target_refuses_when_the_address_is_empty():
    assert_refused(run("e2e", **{VARIABLE: ""}))


def test_the_targets_are_listed_in_make_help():
    done = run("help")
    assert done.returncode == 0
    listed = {line.split()[0] for line in done.stdout.splitlines() if line.split()}
    assert {"e2e", "e2e-install"} <= listed
