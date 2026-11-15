"""scripts/compose_secrets.py (#221): local secrets for the Compose stack. Every value is generated here."""

import importlib.util
import stat
import subprocess
from pathlib import Path

import pytest

from socboard import settings
from socboard.auth import resolve_token_secret
from socboard.ingest import resolve_ingest_key
from socboard.seed import read_seed_passwords

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compose_secrets", ROOT / "scripts" / "compose_secrets.py")
compose_secrets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose_secrets)

DATABASE_PASSWORDS = ("STACK_PG_SUPERUSER_PASSWORD", "STACK_PG_OWNER_PASSWORD", "STACK_PG_APP_PASSWORD")


def values(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_the_names_are_the_apps_make_run_list_plus_three_database_passwords():
    assert compose_secrets.NAMES == tuple(s.name for s in settings.SETTINGS if s.make_run) + DATABASE_PASSWORDS
    assert len(compose_secrets.NAMES) == 8


def test_a_fresh_run_makes_every_name_in_a_private_file_and_prints_no_value(tmp_path, capsys):
    path = tmp_path / "compose.secrets"
    assert compose_secrets.main({}, path) == 0
    made = values(path)
    assert set(made) == set(compose_secrets.NAMES)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    shown = capsys.readouterr()
    for value in made.values():
        assert value not in shown.out + shown.err
    for name in compose_secrets.NAMES:
        assert name in shown.out


def test_the_values_pass_the_apps_own_rules_in_deployed_mode(tmp_path):
    path = tmp_path / "compose.secrets"
    compose_secrets.main({}, path)
    environ = {**values(path), settings.MODE_VAR: settings.DEPLOYED_VALUE, "SOCBOARD_DATABASE_URL": "postgresql+psycopg://x@db/x"}
    assert resolve_token_secret(environ)
    found, problems = read_seed_passwords(environ)
    assert len(found) == 3 and not problems
    assert resolve_ingest_key(environ)
    settings.check_deployed(environ)
    assert all(compose_secrets.URL_SAFE.fullmatch(environ[name]) for name in DATABASE_PASSWORDS)
    assert len({environ[name] for name in compose_secrets.NAMES}) == 8


def test_a_second_run_changes_nothing(tmp_path, capsys):
    path = tmp_path / "compose.secrets"
    compose_secrets.main({}, path)
    first = path.read_bytes()
    capsys.readouterr()
    assert compose_secrets.main({}, path) == 0
    assert path.read_bytes() == first
    assert "added" not in capsys.readouterr().out


def test_it_adds_only_the_missing_names_and_keeps_every_other_line(tmp_path):
    path = tmp_path / "compose.secrets"
    path.write_text("# mine\nSTACK_PG_APP_PASSWORD=Chosen123\nOTHER=1", encoding="utf-8")  # no final newline
    assert compose_secrets.main({}, path) == 0
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# mine\nSTACK_PG_APP_PASSWORD=Chosen123\nOTHER=1\n")
    assert text.count("STACK_PG_APP_PASSWORD") == 1
    assert set(values(path)) == set(compose_secrets.NAMES) | {"OTHER"}


def test_a_name_in_the_environment_is_not_written(tmp_path):
    path = tmp_path / "compose.secrets"
    assert compose_secrets.main({"STACK_PG_OWNER_PASSWORD": "FromEnvironment1"}, path) == 0
    assert "STACK_PG_OWNER_PASSWORD" not in values(path)


def test_a_symlink_is_refused_and_its_target_unchanged(tmp_path, capsys):
    target = tmp_path / "elsewhere"
    target.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "compose.secrets"
    link.symlink_to(target)
    assert compose_secrets.main({}, link) == 2
    assert target.read_text(encoding="utf-8") == "keep\n"
    assert "symbolic link" in capsys.readouterr().err


@pytest.mark.parametrize("where", ["file", "environment"])
def test_an_empty_value_is_refused_naming_the_variable_and_nothing_is_written(tmp_path, capsys, where):
    path = tmp_path / "compose.secrets"
    before = "SOCBOARD_TOKEN_SECRET=\n"
    environ = {}
    if where == "file":
        path.write_text(before, encoding="utf-8")
    else:
        environ["SOCBOARD_TOKEN_SECRET"] = ""
    assert compose_secrets.main(environ, path) == 2
    assert "SOCBOARD_TOKEN_SECRET" in capsys.readouterr().err
    assert (path.read_text(encoding="utf-8") if path.exists() else "") == (before if where == "file" else "")


@pytest.mark.parametrize("bad", ["has/slash", "a:b", "pass word", "p@ss", "100%"])
def test_a_database_password_that_is_not_url_safe_is_refused_without_printing_it(tmp_path, capsys, bad):
    path = tmp_path / "compose.secrets"
    path.write_text(f"STACK_PG_APP_PASSWORD={bad}\n", encoding="utf-8")
    assert compose_secrets.main({}, path) == 2
    shown = capsys.readouterr()
    assert "STACK_PG_APP_PASSWORD" in shown.err and bad not in shown.out + shown.err
    assert path.read_text(encoding="utf-8") == f"STACK_PG_APP_PASSWORD={bad}\n"


def test_the_file_name_is_git_ignored():
    done = subprocess.run(["git", "check-ignore", "-q", "compose.secrets"], cwd=ROOT, check=False)
    assert done.returncode == 0


def test_a_whitespace_only_value_counts_as_empty(tmp_path, capsys):
    path = tmp_path / "compose.secrets"
    assert compose_secrets.main({"STACK_PG_APP_PASSWORD": "   "}, path) == 2
    assert "STACK_PG_APP_PASSWORD" in capsys.readouterr().err
    assert not path.exists()


def test_with_every_name_in_the_environment_an_empty_private_file_is_still_made(tmp_path):
    path = tmp_path / "compose.secrets"
    environ = {name: "Abcdef0123456789Abcdef0123456789" for name in compose_secrets.NAMES}
    assert compose_secrets.main(environ, path) == 0
    assert path.read_bytes() == b"" and stat.S_IMODE(path.stat().st_mode) == 0o600
