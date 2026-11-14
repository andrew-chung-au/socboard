"""`make run`'s preflight (#22): `scripts/run_preflight.py` and the README quick start.

The preflight decides from the text of environment values by calling the app's own resolvers, so
the table below replays it over the forms that matter and a second test checks that it agrees
with the resolvers row by row. The refused `make run` cases run `make` as a subprocess with an
explicit environment (never the caller's) and start nothing. Every value here is synthetic.
"""

import importlib.util
import re
import socket
import subprocess
from pathlib import Path

import pytest
from backend_support import TEST_TOKEN_SECRET
from data_support import SEED_PASSWORDS
from test_audit_api import INGEST_KEY

from socboard.auth import ConfigError, resolve_token_secret
from socboard.ingest import resolve_ingest_key
from socboard.seed import read_seed_passwords

ROOT = Path(__file__).resolve().parents[1]
SECRET = "SOCBOARD_TOKEN_SECRET"
SEED_A, SEED_B, SEED_LEAD = SEED_PASSWORDS
KEY = "SOCBOARD_INGEST_API_KEY"
REQUIRED = (SECRET, SEED_A, SEED_B, SEED_LEAD, KEY)
PID_FILES = (ROOT / ".scratch" / "backend.pid", ROOT / ".scratch" / "frontend.pid")


def load_preflight():
    spec = importlib.util.spec_from_file_location("run_preflight", ROOT / "scripts" / "run_preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = load_preflight()


def good_values():
    """The exact values the existing tests already use."""
    return {SECRET: TEST_TOKEN_SECRET, **SEED_PASSWORDS, KEY: INGEST_KEY}


UNSET = None
# (row id, overrides (UNSET removes the variable), variables the preflight must refuse)
ROWS = [
    ("existing test values", {}, set()),
    ("secret: 32 letters inside leading and trailing whitespace", {SECRET: "  \t" + "s" * 32 + " \n"}, set()),
    ("seed passwords of exactly 12 characters", {SEED_A: "a" * 12, SEED_B: "b" * 12, SEED_LEAD: "l" * 12}, set()),
    # make seed counts raw length, so 12 spaces pass there and here alike.
    ("seed passwords of 12 spaces (accepted, as make seed accepts them)", {SEED_A: " " * 12, SEED_B: " " * 12, SEED_LEAD: " " * 12}, set()),
    # resolve_ingest_key counts raw length too.
    ("ingest key of 16 spaces (accepted, as the backend accepts it)", {KEY: " " * 16}, set()),
    ("ingest key of exactly 16 characters", {KEY: "k" * 16}, set()),
    ("loopback host localhost and port 8001", {"SOCBOARD_HOST": "localhost", "SOCBOARD_PORT": "8001"}, set()),
    ("secret: 32 spaces", {SECRET: " " * 32}, {SECRET}),
    ("secret: 16 spaces then 16 letters", {SECRET: " " * 16 + "s" * 16}, {SECRET}),
    ("secret: 31 letters", {SECRET: "s" * 31}, {SECRET}),
    ("seed A: 11 characters", {SEED_A: "a" * 11}, {SEED_A}),
    ("seed B: 11 characters", {SEED_B: "b" * 11}, {SEED_B}),
    ("seed lead: 11 characters", {SEED_LEAD: "l" * 11}, {SEED_LEAD}),
    ("ingest key: 15 characters", {KEY: "k" * 15}, {KEY}),
    ("port 0", {"SOCBOARD_PORT": "0"}, {"SOCBOARD_PORT"}),
    ("port not a number", {"SOCBOARD_PORT": "80a"}, {"SOCBOARD_PORT"}),
    ("port 65536", {"SOCBOARD_PORT": "65536"}, {"SOCBOARD_PORT"}),
    ("non-loopback host", {"SOCBOARD_HOST": "0.0.0.0"}, {"SOCBOARD_HOST"}),
    ("invalid database URL", {"SOCBOARD_DATABASE_URL": "not a database url"}, {"SOCBOARD_DATABASE_URL"}),
    ("non-loopback host and invalid port together", {"SOCBOARD_HOST": "0.0.0.0", "SOCBOARD_PORT": "x1"}, {"SOCBOARD_HOST", "SOCBOARD_PORT"}),
    (
        "host, port and database URL all invalid",
        {"SOCBOARD_HOST": "0.0.0.0", "SOCBOARD_PORT": "x1", "SOCBOARD_DATABASE_URL": "not a database url"},
        {"SOCBOARD_HOST", "SOCBOARD_PORT", "SOCBOARD_DATABASE_URL"},
    ),
    ("everything required unset", {name: UNSET for name in REQUIRED}, set(REQUIRED)),
]
for _name in REQUIRED:
    ROWS.append((f"{_name} unset", {_name: UNSET}, {_name}))
    ROWS.append((f"{_name} empty", {_name: ""}, {_name}))


def environ_for(overrides, tmp_path):
    environ = {**good_values(), "SOCBOARD_DATABASE_URL": "sqlite:///" + (tmp_path / "pf.db").as_posix()}
    for name, value in overrides.items():
        if value is UNSET:
            environ.pop(name, None)
        else:
            environ[name] = value
    return environ


@pytest.mark.parametrize(("overrides", "refused"), [row[1:] for row in ROWS], ids=[row[0] for row in ROWS])
def test_the_preflight_refuses_exactly_the_forms_the_app_refuses(overrides, refused, tmp_path, capsys):
    environ = environ_for(overrides, tmp_path)
    problems = preflight.check(environ)
    assert {name for name, _ in problems} == refused
    assert len(problems) == len(refused)  # one line per failing variable
    for name, message in problems:
        assert name in message
    for name in REQUIRED:
        if environ.get(name, "").strip():
            assert not any(environ[name] in message for _, message in problems)
    assert capsys.readouterr().err == ""  # the ingest resolver's own warning is not printed
    assert not (tmp_path / "pf.db").exists()


@pytest.mark.parametrize("overrides", [row[1] for row in ROWS], ids=[row[0] for row in ROWS])
def test_the_preflight_agrees_with_the_apps_resolvers(overrides, tmp_path):
    environ = environ_for(overrides, tmp_path)
    named = {name for name, _ in preflight.check(environ)}
    try:
        resolve_token_secret(environ)
        secret_refused = False
    except ConfigError:
        secret_refused = True
    assert (SECRET in named) == secret_refused
    found, _ = read_seed_passwords(environ)
    for username, variable in (("analyst-a", SEED_A), ("analyst-b", SEED_B), ("lead", SEED_LEAD)):
        assert (variable in named) == (username not in found)
    assert (KEY in named) == (resolve_ingest_key(environ) is None)


def test_the_required_list_is_the_five_variables():
    assert preflight.REQUIRED == REQUIRED


# --- make run refuses and starts nothing --------------------------------------------------------


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", port)) == 0


SHORT_FORMS = [
    (SECRET, "s" * 31),
    (SECRET, " " * 32),
    (SECRET, " " * 16 + "s" * 16),
    (SEED_A, "a" * 11),
    (SEED_B, "b" * 11),
    (SEED_LEAD, "l" * 11),
    (KEY, "k" * 15),
]
MAKE_CASES = [(name, UNSET) for name in REQUIRED] + SHORT_FORMS


@pytest.mark.parametrize(
    ("variable", "value"),
    MAKE_CASES,
    ids=[f"{name}-{'unset' if value is None else repr(value)}" for name, value in MAKE_CASES],
)
def test_make_run_refuses_and_starts_nothing(variable, value, tmp_path):
    database_file = tmp_path / "run.db"
    port = free_port()
    environ = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "SOCBOARD_DATABASE_URL": "sqlite:///" + database_file.as_posix(),
        "SOCBOARD_PORT": str(port),
        **good_values(),
    }
    if value is UNSET:
        del environ[variable]
    else:
        environ[variable] = value
    before = [path.exists() for path in PID_FILES]

    done = subprocess.run(
        ["make", "--no-print-directory", "run"], cwd=ROOT, env=environ, capture_output=True, text=True, timeout=60
    )

    output = done.stdout + done.stderr
    assert done.returncode != 0
    assert variable in output
    for name, synthetic in good_values().items():
        if name != variable:
            assert synthetic not in output
    if value is not UNSET and value.strip():
        assert value not in output
    assert [path.exists() for path in PID_FILES] == before
    assert not database_file.exists()
    assert not listening(port)


def test_make_run_names_every_bad_bind_and_url_setting_one_line_each(tmp_path):
    bad = {"SOCBOARD_HOST": "0.0.0.0", "SOCBOARD_PORT": "port-x91", "SOCBOARD_DATABASE_URL": "zz-not-a-db-url"}
    environ = {"PATH": "/usr/local/bin:/usr/bin:/bin", **good_values(), **bad}
    before = [path.exists() for path in PID_FILES]

    done = subprocess.run(
        ["make", "--no-print-directory", "run"], cwd=ROOT, env=environ, capture_output=True, text=True, timeout=60
    )

    output = done.stdout + done.stderr
    assert done.returncode != 0
    for name in bad:
        lines = [line for line in output.splitlines() if name in line]
        assert len(lines) == 1, (name, lines)
        assert not any(other in lines[0] for other in bad if other != name)
    for value in [*bad.values(), *good_values().values()]:
        assert value not in output
    assert [path.exists() for path in PID_FILES] == before


# --- README quick start -------------------------------------------------------------------------


def test_the_readme_names_every_required_variable_and_holds_no_value():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in preflight.REQUIRED:
        assert name in readme
    assert [line for line in readme.splitlines() if re.search(r"SOCBOARD_[A-Z_]+=\S", line)] == []
