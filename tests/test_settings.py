"""The runtime settings list and deployed mode (#141).

Every value here is synthetic. Start-up refusals go through `server.main`, which returns before
uvicorn starts when the app cannot be built.
"""

import importlib.util
import logging
import re
import subprocess
from pathlib import Path

import pytest
from backend_support import TEST_TOKEN_SECRET, sqlite_url

from socboard import database, ingest, server, settings, spa
from socboard.app import create_app
from socboard.auth import SECRET_ENV_NAME, ConfigError
from socboard.seed import USERS

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ENV = ROOT / ".env.example"
NAMES = [setting.name for setting in settings.SETTINGS]
SECRET = SECRET_ENV_NAME
KEY = ingest.KEY_ENV_NAME
DB = database.ENV_NAME
MODE = settings.MODE_VAR
GOOD_KEY = "test-only-ingest-key-0123456789"
BUILD_ARGUMENTS = ("VITE_API_BASE_URL", "VITE_FEATURE_REASSIGN", "VITE_POLL_INTERVAL_MS", "VITE_USE_MOCK")


def deployed_environ(tmp_path, **overrides):
    environ = {MODE: "deployed", SECRET: TEST_TOKEN_SECRET, KEY: GOOD_KEY, DB: sqlite_url(tmp_path / "d.db")}
    for name, value in overrides.items():
        if value is None:
            environ.pop(name)
        else:
            environ[name] = value
    return environ


# --- the list -----------------------------------------------------------------------------------


def test_the_list_names_every_runtime_variable_once():
    seed_names = [variable for _, _, _, variable in USERS]
    expected = {SECRET, KEY, DB, *seed_names, "SOCBOARD_HOST", "SOCBOARD_PORT", spa.STATIC_DIR_VAR, MODE}
    assert set(NAMES) == expected
    assert len(NAMES) == len(set(NAMES))
    assert spa.STATIC_DIR_VAR == "SOCBOARD_STATIC_DIR"
    assert all(setting.rule for setting in settings.SETTINGS)


def test_secret_and_deployed_flags():
    by_name = {setting.name: setting for setting in settings.SETTINGS}
    assert {name for name, s in by_name.items() if s.deployed} == {SECRET, KEY, DB}
    assert {name for name, s in by_name.items() if s.secret} == {SECRET, KEY, DB, *(v for *_, v in USERS)}


def test_the_preflight_takes_its_required_names_from_the_list():
    spec = importlib.util.spec_from_file_location("run_preflight", ROOT / "scripts" / "run_preflight.py")
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    assert preflight.REQUIRED == tuple(s.name for s in settings.SETTINGS if s.make_run)


def test_the_readme_documents_exactly_the_listed_names():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in NAMES:
        assert f"| `{name}` |" in readme, name
    documented = set(re.findall(r"SOCBOARD_[A-Z0-9_]+", readme))
    assert documented <= set(NAMES), documented - set(NAMES)


def test_build_arguments_are_in_the_readme_and_not_runtime_variables():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    build_line = next(line for line in readme.splitlines() if line.startswith("Build arguments, not runtime variables"))
    for name in BUILD_ARGUMENTS:
        assert f"`{name}`" in build_line
        assert name not in NAMES


def test_the_example_env_file_lists_names_only():
    lines = [line for line in EXAMPLE_ENV.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    assert lines == [f"{name}=" for name in NAMES]


def test_the_kit_secret_scan_reports_nothing_on_the_example_env_file():
    done = subprocess.run(
        ["bash", "_docs/agent-kit/scripts/scan-issue-body.sh", str(EXAMPLE_ENV)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == ""


# --- the switch ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        pytest.param({}, False, id="unset"),
        pytest.param({MODE: ""}, False, id="empty"),
        pytest.param({MODE: "deployed"}, True, id="exact value"),
        pytest.param({MODE: "Deployed"}, False, id="near miss: case"),
        pytest.param({MODE: " deployed"}, False, id="near miss: leading space"),
        pytest.param({MODE: "deployed "}, False, id="near miss: trailing space"),
        pytest.param({MODE: "deploy"}, False, id="near miss: prefix"),
        pytest.param({MODE: "1"}, False, id="near miss: 1"),
    ],
)
def test_only_the_exact_value_turns_deployed_mode_on(environ, expected):
    assert settings.is_deployed(environ) is expected


def test_a_near_miss_starts_like_development(tmp_path, caplog):
    environ = deployed_environ(tmp_path, **{MODE: "Deployed", KEY: None})
    with caplog.at_level(logging.WARNING, logger="socboard.ingest"):
        app = create_app(environ)
    assert app.state.ingest_api_key is None
    assert len([r for r in caplog.records if r.name == "socboard.ingest"]) == 1


# --- deployed mode refuses at start-up ----------------------------------------------------------


def test_a_complete_deployed_environment_builds_the_app(tmp_path):
    app = create_app(deployed_environ(tmp_path))
    assert app.state.ingest_api_key == GOOD_KEY


REFUSED = [
    pytest.param(SECRET, None, id="secret missing"),
    pytest.param(SECRET, "", id="secret empty"),
    pytest.param(SECRET, "Qz7-secret-31-characters-long-x", id="secret too short"),
    pytest.param(SECRET, " " * 32, id="secret 32 spaces"),
    pytest.param(SECRET, "s" * 32, id="secret 32 of one character"),
    pytest.param(KEY, None, id="key missing"),
    pytest.param(KEY, "", id="key empty"),
    pytest.param(KEY, "Qz7-key-15-char", id="key too short"),
    pytest.param(KEY, " " * 16, id="key 16 spaces"),
    pytest.param(KEY, "k" * 16, id="key 16 of one character"),
    pytest.param(KEY, "   Qz7-key-13ch   ", id="key short without its spaces"),
    pytest.param(DB, None, id="database URL missing"),
    pytest.param(DB, "", id="database URL empty"),
    pytest.param(DB, "   ", id="database URL blank"),
    pytest.param(DB, "Qz7", id="database URL too short to be a URL"),
]


@pytest.mark.parametrize(("variable", "value"), REFUSED)
def test_deployed_mode_refuses_a_missing_or_unusable_value(variable, value, tmp_path, capsys, caplog):
    environ = deployed_environ(tmp_path, **{variable: value})
    with caplog.at_level(logging.DEBUG):
        assert server.main(environ) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert variable in out.err
    if value and value.strip():
        assert value.strip() not in out.err + caplog.text
    assert not (tmp_path / "d.db").exists()


def test_the_token_secret_rule_on_spaces_holds_in_every_mode(tmp_path):
    environ = deployed_environ(tmp_path, **{MODE: None, SECRET: " " * 32})
    with pytest.raises(ConfigError):
        create_app(environ)


def test_outside_deployed_mode_one_repeated_character_is_still_accepted(tmp_path):
    # The existing #22 and #104 tests accept "s" * 32 and "y" * 32 outside deployed mode, and must
    # pass unchanged, so the repeated-character rule applies in deployed mode only (issue comment).
    environ = deployed_environ(tmp_path, **{MODE: None, SECRET: "s" * 32, KEY: "k" * 16})
    app = create_app(environ)
    assert app.state.ingest_api_key == "k" * 16


def test_outside_deployed_mode_the_ingest_key_stays_optional(tmp_path, caplog):
    environ = deployed_environ(tmp_path, **{MODE: None, KEY: None, DB: None})
    with caplog.at_level(logging.WARNING, logger="socboard.ingest"):
        app = create_app(environ)
    assert app.state.ingest_api_key is None
    assert len([r for r in caplog.records if r.name == "socboard.ingest"]) == 1


# --- no value in any output ---------------------------------------------------------------------

DISTINCT_SECRET = "Zx9-distinct-secret-value-0000000000-QQ"
DISTINCT_KEY = "Zx9-distinct-key-QQ"
DISTINCT_DB = "Zx9-distinct-database-QQ"
DISTINCT_SHORT_KEY = "Zx9-short-QQ"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({SECRET: "Zx9-short-secret-QQ", KEY: DISTINCT_KEY, DB: DISTINCT_DB}, id="deployed, bad secret"),
        pytest.param({SECRET: DISTINCT_SECRET, KEY: DISTINCT_SHORT_KEY, DB: DISTINCT_DB}, id="deployed, bad key"),
        pytest.param({SECRET: DISTINCT_SECRET, KEY: DISTINCT_KEY + "-long-enough", DB: DISTINCT_DB}, id="deployed, bad URL"),
        pytest.param({MODE: None, SECRET: DISTINCT_SECRET, KEY: DISTINCT_SHORT_KEY, DB: DISTINCT_DB}, id="development, bad URL"),
    ],
)
def test_no_value_appears_in_any_output(overrides, tmp_path, capsys, caplog):
    environ = deployed_environ(tmp_path, **overrides)
    with caplog.at_level(logging.DEBUG):
        assert server.main(environ) == 2
    out = capsys.readouterr()
    captured = out.out + out.err + caplog.text
    for value in environ.values():
        if value != "deployed":
            assert value not in captured
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for value in (DISTINCT_SECRET, DISTINCT_KEY, DISTINCT_DB, DISTINCT_SHORT_KEY, TEST_TOKEN_SECRET, GOOD_KEY):
        assert value not in readme
