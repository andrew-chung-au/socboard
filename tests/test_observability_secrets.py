"""scripts/observability_secrets.py (#293): Grafana's admin password, in its own git-ignored file.

Every value here is synthetic; the script's output is checked for the value never appearing.
"""

import importlib.util
import os
import stat
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "observability_secrets.py"
spec = importlib.util.spec_from_file_location("observability_secrets", SCRIPT)
secrets_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secrets_script)

NAME = secrets_script.NAME


def value_in(path):
    return secrets_script.read_file_value(path.read_text(encoding="utf-8"))


def test_the_first_run_writes_a_random_password_mode_0600_and_prints_only_the_name(tmp_path, capsys):
    path = tmp_path / "observability.secrets"
    assert secrets_script.main({}, path) == 0
    value = value_in(path)
    assert value and len(value) == 48 and value.isalnum()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert NAME in out and value not in out


def test_two_runs_make_two_different_files_but_one_run_twice_changes_nothing(tmp_path, capsys):
    first, second = tmp_path / "a", tmp_path / "b"
    secrets_script.main({}, first)
    secrets_script.main({}, second)
    assert value_in(first) != value_in(second)
    before = first.read_text(encoding="utf-8")
    capsys.readouterr()
    assert secrets_script.main({}, first) == 0
    assert first.read_text(encoding="utf-8") == before
    assert capsys.readouterr().out == "observability secrets: already set\n"


def test_an_existing_line_is_never_rewritten_and_other_lines_are_kept(tmp_path):
    path = tmp_path / "observability.secrets"
    path.write_text("OTHER=keep-me\nSTACK_GRAFANA_ADMIN_PASSWORD=chosen-by-the-human\n", encoding="utf-8")
    assert secrets_script.main({}, path) == 0
    assert path.read_text(encoding="utf-8") == "OTHER=keep-me\nSTACK_GRAFANA_ADMIN_PASSWORD=chosen-by-the-human\n"


def test_a_file_without_the_name_gets_it_appended_after_a_missing_final_newline(tmp_path):
    path = tmp_path / "observability.secrets"
    path.write_text("OTHER=keep-me", encoding="utf-8")
    assert secrets_script.main({}, path) == 0
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "OTHER=keep-me" and lines[1].startswith(f"{NAME}=") and len(lines) == 2


def test_a_value_in_the_environment_is_not_written_but_the_file_exists_for_compose(tmp_path, capsys):
    path = tmp_path / "observability.secrets"
    assert secrets_script.main({NAME: "from-the-environment"}, path) == 0
    assert path.exists() and path.read_text(encoding="utf-8") == ""
    assert "from-the-environment" not in capsys.readouterr().out


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_value_is_refused_and_nothing_is_written(tmp_path, blank, capsys):
    path = tmp_path / "observability.secrets"
    assert secrets_script.main({NAME: blank}, path) == 2
    assert not path.exists() and "empty value" in capsys.readouterr().err
    path.write_text(f"{NAME}=\n", encoding="utf-8")
    assert secrets_script.main({}, path) == 2
    assert path.read_text(encoding="utf-8") == f"{NAME}=\n"


def test_a_symbolic_link_is_refused_and_its_target_is_untouched(tmp_path, capsys):
    target = tmp_path / "target"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "observability.secrets"
    os.symlink(target, link)
    assert secrets_script.main({}, link) == 2
    assert target.read_text(encoding="utf-8") == "keep" and "symbolic link" in capsys.readouterr().err
