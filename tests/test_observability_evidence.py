"""observability/screenshots/manifest.py (#293): the manifest of the observability evidence.

Every file here is synthetic. Evidence folders that exist in the repository are checked too, so a
committed screenshot set without a whole manifest, or with a canary string in it, fails here.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("observability_manifest", ROOT / "observability" / "screenshots" / "manifest.py")
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)

RUN_URL = "https://github.com/example-owner/example-repo/actions/runs/123456789"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
PNG = manifest.PNG + b"synthetic"


def make_folder(tmp_path, files=None):
    folder = tmp_path / "2026-11-17"
    folder.mkdir()
    for name in files or manifest.SHOWS:
        (folder / name).write_bytes(PNG if name.endswith(".png") else b'{"synthetic": true}')
    return folder


def built(tmp_path, files=None):
    folder = make_folder(tmp_path, files)
    manifest.build(folder, "123456789", RUN_URL, COMMIT)
    return folder


def test_a_built_folder_passes_the_check_and_describes_every_file(tmp_path):
    folder = built(tmp_path)
    assert manifest.check(folder) == []
    described = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert described["run_id"] == "123456789" and described["run_url"] == RUN_URL and described["commit"] == COMMIT
    assert [entry["name"] for entry in described["files"]] == sorted(manifest.SHOWS)
    assert all(entry["shows"] for entry in described["files"])


def test_the_contracts_seven_canary_strings_are_read_from_the_contract():
    values = manifest.canaries()
    assert len(values) == 7 and all(value.startswith("CANARY-") for value in values)


@pytest.mark.parametrize("name", ["trace.json", "logs.json", "exemplars.json", "window.json"])
def test_a_canary_string_in_a_file_fails_and_names_the_file(tmp_path, name):
    folder = built(tmp_path)
    (folder / name).write_text(json.dumps({"x": manifest.canaries()[0]}), encoding="utf-8")
    assert manifest.check(folder) == [f"{name} holds a canary string of the contract"]


def test_a_secret_looking_line_in_a_json_file_fails(tmp_path):
    folder = built(tmp_path)
    (folder / "logs.json").write_text(json.dumps({"x": "STACK_GRAFANA_ADMIN_PASSWORD=abcdef0123456789"}), encoding="utf-8")
    assert any("looks like a secret" in line for line in manifest.check(folder))


def test_a_file_missing_from_the_folder_or_the_manifest_fails(tmp_path):
    folder = built(tmp_path)
    (folder / "trace.png").unlink()
    assert manifest.check(folder) == ["trace.png is in the manifest but not in the folder"]
    second = tmp_path / "second"
    second.mkdir()
    folder = built(second)
    (folder / "extra.png").write_bytes(PNG)
    assert manifest.check(folder) == ["extra.png is in the folder but not in the manifest"]


def test_a_bad_png_or_bad_json_fails(tmp_path):
    folder = built(tmp_path)
    (folder / "dashboard.png").write_bytes(b"not a png")
    (folder / "trace.json").write_text("not json", encoding="utf-8")
    assert manifest.check(folder) == ["dashboard.png is not a PNG", "trace.json is not JSON"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("run_id", "abc", "run_id is not a run number"),
        ("run_url", "https://example.com/runs/1", "run_url is not a GitHub Actions run URL"),
        ("run_url", "https://github.com/o/r/actions/runs/999", "run_url does not end in the run_id"),
        ("commit", "abc123", "commit is not a full commit hash"),
    ],
)
def test_a_manifest_with_a_wrong_header_field_fails(tmp_path, field, value, expected):
    folder = built(tmp_path)
    described = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    described[field] = value
    (folder / "manifest.json").write_text(json.dumps(described), encoding="utf-8")
    assert expected in manifest.check(folder)


def test_no_manifest_or_a_broken_one_fails(tmp_path):
    folder = make_folder(tmp_path)
    assert manifest.check(folder) == ["manifest.json is missing"]
    (folder / "manifest.json").write_text("nope", encoding="utf-8")
    assert manifest.check(folder) == ["manifest.json is not JSON"]
    (folder / "manifest.json").write_text("[]", encoding="utf-8")
    assert manifest.check(folder) == ["manifest.json is not an object"]
    (folder / "manifest.json").write_text(json.dumps({"run_id": "1", "run_url": RUN_URL, "commit": COMMIT, "files": []}), encoding="utf-8")
    assert "files is not a list of files" in manifest.check(folder)


def test_build_refuses_a_file_it_has_no_description_for(tmp_path):
    folder = make_folder(tmp_path, ["dashboard.png", "mystery.bin"])
    with pytest.raises(ValueError, match="mystery.bin"):
        manifest.build(folder, "1", RUN_URL, COMMIT)


def test_the_command_line_builds_then_checks_and_prints_no_file_content(tmp_path, capsys):
    folder = make_folder(tmp_path)
    assert manifest.main(["build", str(folder), "--run-id", "123456789", "--run-url", RUN_URL, "--commit", COMMIT]) == 0
    assert capsys.readouterr().out == "observability manifest: ok\n"
    assert manifest.main(["check", str(folder)]) == 0


def evidence_folders():
    base = ROOT / "_docs" / "evidence" / "observability"
    return sorted(path for path in base.glob("*") if path.is_dir()) if base.is_dir() else []


def test_every_evidence_folder_in_the_repository_is_whole_and_holds_no_canary():
    for folder in evidence_folders():
        assert manifest.check(folder) == [], folder.name
