"""Tests for scripts/check_python_version.py (#100)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_python_version.py"
_spec = importlib.util.spec_from_file_location("check_python_version", _SCRIPT)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".python-version"
    path.write_text(text)
    return path


def test_matching_version_passes(tmp_path):
    assert checker.check(_file(tmp_path, "3.12\n"), (3, 12)) is None


def test_different_interpreter_names_both_versions(tmp_path):
    message = checker.check(_file(tmp_path, "3.12\n"), (3, 14))
    assert "3.14" in message and "3.12" in message


def test_missing_file_fails(tmp_path):
    assert checker.check(tmp_path / ".python-version", (3, 12)) is not None


@pytest.mark.parametrize("text", ["", "\n  \n", "3.12\n3.13\n", "3.12.3\n", "3\n", "python3.12\n"])
def test_malformed_file_fails_even_when_it_starts_with_the_running_version(tmp_path, text):
    assert "minor version only" in checker.check(_file(tmp_path, text), (3, 12))


def test_repository_file_matches_this_interpreter():
    assert checker.check(checker.VERSION_FILE, sys.version_info[:2]) is None
