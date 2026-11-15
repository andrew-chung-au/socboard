"""e2e/redact_traces.py (#234): a failure artifact leaves CI with none of the run's secrets in it.

Every value here is made up for the test.
"""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("redact_traces", ROOT / "e2e" / "redact_traces.py")
redact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redact)

PASSWORD = "Pw0123456789abcdefPw0123456789abcdef"
KEY = "Key0123456789abcdefKey0123456789abcdef"
TOKEN = "eyJhbGciOiJIUzI1NiJ9.payload-part.signature-part"
SECRETS = f"SOCBOARD_SEED_PASSWORD_LEAD={PASSWORD}\nSOCBOARD_INGEST_API_KEY={KEY}\nSHORT=abc\n"
PNG = b"\x89PNG\r\n" + PASSWORD.encode() + b"\x00"  # an image is left alone: only what is on screen


def make_results(tmp_path):
    results = tmp_path / "test-results"
    (results / "journey").mkdir(parents=True)
    with zipfile.ZipFile(results / "journey" / "trace.zip", "w") as archive:
        archive.writestr("trace.trace", f'{{"method":"fill","params":{{"value":"{PASSWORD}"}}}}\n')
        archive.writestr("trace.network", f'{{"headers":[{{"name":"Authorization","value":"Bearer {TOKEN}"}}]}}\n')
        archive.writestr("resources/abc123", f'{{"token":"{TOKEN}","user":"lead"}}')
        archive.writestr("resources/page.png", PNG)
        archive.writestr("resources/keep.txt", "nothing secret, SHORT=abc stays\n")
    (results / "journey" / "error-context.md").write_text(f"- textbox: {KEY}\n- heading: Open queue\n", encoding="utf-8")
    (results / "journey" / "test-failed-1.png").write_bytes(PNG)
    secrets_file = tmp_path / "compose.secrets"
    secrets_file.write_text(SECRETS, encoding="utf-8")
    return results, secrets_file


def everything(results):
    found = {}
    for path in results.rglob("*"):
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                found.update({f"{path.name}:{i.filename}": archive.read(i) for i in archive.infolist()})
        elif path.is_file():
            found[path.name] = path.read_bytes()
    return found


def test_no_secret_value_token_or_bearer_is_left_and_nothing_else_is_lost(tmp_path, capsys):
    results, secrets_file = make_results(tmp_path)
    assert redact.main([str(results), str(secrets_file)]) == 0
    found = everything(results)
    for name, data in found.items():
        if not name.endswith(".png"):
            assert PASSWORD.encode() not in data and KEY.encode() not in data and TOKEN.encode() not in data, name
    assert b"[redacted]" in found["trace.zip:trace.trace"]
    assert b"Bearer [redacted]" in found["trace.zip:trace.network"]
    assert found["trace.zip:resources/abc123"] == b'{"token":"[redacted]","user":"lead"}'
    assert b"Open queue" in found["error-context.md"] and b"[redacted]" in found["error-context.md"]
    assert found["trace.zip:resources/keep.txt"] == b"nothing secret, SHORT=abc stays\n"  # a short value is not a secret
    assert found["trace.zip:resources/page.png"] == PNG and found["test-failed-1.png"] == PNG
    shown = capsys.readouterr()
    assert PASSWORD not in shown.out + shown.err and KEY not in shown.out + shown.err and TOKEN not in shown.out + shown.err


def test_a_second_run_changes_nothing(tmp_path):
    results, secrets_file = make_results(tmp_path)
    redact.main([str(results), str(secrets_file)])
    first = everything(results)
    assert redact.main([str(results), str(secrets_file)]) == 0
    assert everything(results) == first


def test_a_passing_run_has_no_results_folder_and_that_is_fine(tmp_path, capsys):
    secrets_file = tmp_path / "compose.secrets"
    secrets_file.write_text(SECRETS, encoding="utf-8")
    assert redact.main([str(tmp_path / "absent"), str(secrets_file)]) == 0
    assert "nothing to redact" in capsys.readouterr().out


@pytest.mark.parametrize("content", [None, "", "SHORT=abc\n# only a comment\n"])
def test_with_no_usable_secrets_file_it_refuses_and_changes_nothing(tmp_path, content):
    results, secrets_file = make_results(tmp_path)
    before = everything(results)
    if content is None:
        secrets_file.unlink()
    else:
        secrets_file.write_text(content, encoding="utf-8")
    assert redact.main([str(results), str(secrets_file)]) == 2
    assert everything(results) == before


def test_a_zip_that_cannot_be_read_fails_naming_the_file_and_never_a_value(tmp_path, capsys):
    results, secrets_file = make_results(tmp_path)
    (results / "journey" / "broken.zip").write_bytes(b"not a zip " + PASSWORD.encode())
    assert redact.main([str(results), str(secrets_file)]) == 1
    shown = capsys.readouterr()
    assert "broken.zip" in shown.err and PASSWORD not in shown.out + shown.err


def test_a_value_that_survives_the_replacement_fails_the_run(tmp_path, monkeypatch, capsys):
    results, secrets_file = make_results(tmp_path)
    monkeypatch.setattr(redact, "scrub", lambda data, values: data)  # a redactor that does nothing
    assert redact.main([str(results), str(secrets_file)]) == 1
    assert "could not make" in capsys.readouterr().err
