"""scripts/ci_deploy.py (#238): the dev deploy workflow's logic, run against a stub `aws` and a stub health check.

Nothing here touches AWS or the network. `subprocess.run` is replaced, so the script's real argument lists are
what the stub records. Every value is synthetic.
"""

import importlib.util
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_deploy", ROOT / "scripts" / "ci_deploy.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

REPOSITORY = "Andrew-Chung-AU/socboard"
SHA = "a" * 40
OTHER_SHA = "b" * 40
DIGEST = "sha256:" + "c" * 64
TAG = "20261116-010203-aaaaaaa"
IMAGE = "ghcr.io/andrew-chung-au/socboard"
INSTANCE = "i-0123456789abcdef0"
URL = "https://dev.example.test"
COMMAND_ID = "11111111-2222-3333-4444-555555555555"
CANARY = "CANARY-secret-value-4f9c"


def artifact(**changes):
    data = {"schema": 1, "image": IMAGE, "tag": TAG, "digest": DIGEST, "revision": SHA}
    for key, value in changes.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    return json.dumps(data)


# --- find ----------------------------------------------------------------------------------------


def run_find(tmp_path, text, main_head=SHA, head_sha=SHA, present=True):
    path = tmp_path / "published-image.json"
    if present:
        path.write_text(text, encoding="utf-8")
    out = tmp_path / "out.txt"
    with out.open("w", encoding="utf-8") as handle:
        argv = ["find", "--repository", REPOSITORY, "--head-sha", head_sha, "--main-head", main_head, "--file", str(path)]
        code = ci.main(argv, {}, handle)
    return code, out.read_text(encoding="utf-8")


def test_find_hands_over_the_four_values_of_a_good_artifact(tmp_path, capsys):
    code, out = run_find(tmp_path, artifact())
    assert code == 0
    assert out.splitlines() == ["deploy=true", f"image={IMAGE}", f"tag={TAG}", f"digest={DIGEST}", f"revision={SHA}"]


def test_find_with_no_artifact_ends_green_and_says_nothing_was_published(tmp_path, capsys):
    code, out = run_find(tmp_path, "", present=False)
    assert code == 0 and out == "deploy=false\n"
    assert "published nothing" in capsys.readouterr().out


def test_find_skips_a_publish_that_main_has_moved_past(tmp_path, capsys):
    code, out = run_find(tmp_path, artifact(), main_head=OTHER_SHA)
    assert code == 0 and out == "deploy=false\n"
    assert "superseded" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("not json", "not JSON"),
        ("[]", "one JSON object"),
        (artifact(extra="x"), "exactly the keys"),
        (artifact(digest=None), "exactly the keys"),
        (artifact(schema=2), "schema"),
        (artifact(schema=True), "schema"),
        (artifact(image="ghcr.io/someone-else/socboard"), "image"),
        (artifact(image=7), "image is not a string"),
        (artifact(tag="latest"), "tag"),
        (artifact(tag="20261116-010203-AAAAAAA"), "tag"),
        (artifact(digest="sha256:" + "c" * 63), "digest"),
        (artifact(digest="sha256:" + "C" * 64), "digest"),
        (artifact(digest=DIGEST + "\n"), "digest"),
        (artifact(digest="latest"), "digest"),
        (artifact(revision="a" * 39), "revision"),
        (artifact(revision=OTHER_SHA), "not the publish run's commit"),
    ],
)
def test_find_refuses_a_malformed_or_foreign_artifact_naming_the_field(tmp_path, capsys, text, field):
    code, out = run_find(tmp_path, text)
    captured = capsys.readouterr()
    assert code == 1 and out == ""
    assert field in captured.err
    assert "Traceback" not in captured.err


def test_find_never_prints_the_value_of_a_refused_field(tmp_path, capsys):
    run_find(tmp_path, artifact(digest="sha256:" + CANARY))
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err


def test_find_refuses_a_head_of_main_it_could_not_read(tmp_path, capsys):
    code, _ = run_find(tmp_path, artifact(), main_head="")
    assert code == 1 and "head of main" in capsys.readouterr().err


# --- deploy --------------------------------------------------------------------------------------


class Stub:
    """A stand-in for `aws`: canned answers, every call recorded."""

    def __init__(self, invocations, send=None, send_code=0):
        self.calls = []
        self.invocations = list(invocations)
        self.send = send if send is not None else json.dumps({"Command": {"CommandId": COMMAND_ID}})
        self.send_code = send_code

    def __call__(self, argv, **kwargs):
        assert argv[0] == "aws" and kwargs.get("shell") is None
        self.calls.append(argv[1:])
        assert len(self.calls) <= 1000, "the script kept calling aws (a poll with no limit)"
        if argv[2] == "send-command":
            return subprocess.CompletedProcess(argv, self.send_code, self.send, "")
        answer = self.invocations.pop(0) if len(self.invocations) > 1 else self.invocations[0]
        if isinstance(answer, tuple):
            return subprocess.CompletedProcess(argv, answer[0], "", answer[1])
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")


def inv(status="Success", code=0, out=ci.DEPLOYED + "\n", err=""):
    return {"Status": status, "ResponseCode": code, "StandardOutputContent": out, "StandardErrorContent": err}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def world(monkeypatch, tmp_path):
    clock = Clock()
    state = SimpleNamespace(stub=None, health=[200], tries=[], clock=clock, tmp=tmp_path)

    def fake_http(url, timeout):
        state.tries.append((url, timeout))
        return state.health.pop(0) if len(state.health) > 1 else state.health[0]

    monkeypatch.setattr(ci, "http_status", fake_http)

    def go(stub, env=None, **changes):
        state.stub = stub
        monkeypatch.setattr(subprocess, "run", stub)
        values = dict(digest=DIGEST, tag=TAG, revision=SHA, image=IMAGE, run_id="123456", run_url="https://github.com/o/r/actions/runs/123456",
                      record=str(tmp_path / "record.json"), summary=str(tmp_path / "summary.md"))
        values.update(changes)
        args = SimpleNamespace(**values)
        environment = {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL, "AWS_SECRET_ACCESS_KEY": CANARY} if env is None else env
        return ci.deploy(args, environment, clock=clock, sleep=clock.sleep, now=lambda: __import__("datetime").datetime(2026, 11, 16, 1, 2, 3, tzinfo=__import__("datetime").timezone.utc))

    state.go = go
    state.record = lambda: json.loads((tmp_path / "record.json").read_text(encoding="utf-8")) if (tmp_path / "record.json").exists() else None
    return state


def test_a_deploy_sends_exactly_one_document_with_the_digest_and_nothing_else(world):
    world.go(Stub([inv()]))
    assert world.stub.calls[0] == [
        "ssm", "send-command", "--region", "us-east-1", "--document-name", "socboard-deploy", "--instance-ids", INSTANCE,
        "--parameters", f"digest={DIGEST}", "--comment", f"socboard deploy {SHA[:7]}", "--output", "json",
    ]
    assert world.stub.calls[1] == ["ssm", "get-command-invocation", "--region", "us-east-1", "--command-id", COMMAND_ID, "--instance-id", INSTANCE, "--output", "json"]
    assert {call[1] for call in world.stub.calls} == {"send-command", "get-command-invocation"}
    forbidden = ("--targets", "--output-s3-bucket-name", "--output-s3-key-prefix", "--cli-input-json", "--document-version")
    assert not any(flag in call for call in world.stub.calls for flag in forbidden)


def test_a_deploy_passes_and_writes_the_record_with_exactly_its_keys(world, capsys):
    assert world.go(Stub([inv()])) == 0
    record = world.record()
    assert list(record) == list(ci.RECORD_KEYS)
    assert record == {
        "schema": 1, "environment": "dev", "result": "deployed", "image": IMAGE, "digest": DIGEST, "tag": TAG, "revision": SHA,
        "deployed_at": "2026-11-16T01:02:03Z", "run_id": "123456", "run_url": "https://github.com/o/r/actions/runs/123456", "command_id": COMMAND_ID,
    }
    assert world.tries == [(URL, 10)]
    summary = (world.tmp / "summary.md").read_text(encoding="utf-8")
    assert DIGEST in summary and "deployed" in summary and "200" in summary


def test_already_deployed_is_a_pass(world):
    assert world.go(Stub([inv(out=ci.ALREADY + "\n")])) == 0
    assert world.record()["result"] == "already deployed"


def test_the_poll_waits_while_the_command_runs(world):
    stub = Stub([inv("Pending", None, ""), inv("InProgress", None, ""), inv("Delayed", None, ""), inv()])
    assert world.go(stub) == 0
    assert len(stub.calls) == 1 + 4
    assert world.clock.now == 15


def test_invocation_does_not_exist_just_after_sending_is_retried(world):
    stub = Stub([(254, "An error occurred (InvocationDoesNotExist) when calling the GetCommandInvocation operation"), inv()])
    assert world.go(stub) == 0
    assert len(stub.calls) == 3


def test_invocation_does_not_exist_after_a_minute_fails(world):
    stub = Stub([(254, "An error occurred (InvocationDoesNotExist)")])
    with pytest.raises(ci.Refused, match="exit 254"):
        world.go(stub)
    assert world.clock.now >= 60 and world.record() is None


def test_another_aws_error_is_not_retried(world):
    stub = Stub([(255, "AccessDeniedException")])
    with pytest.raises(ci.Refused):
        world.go(stub)
    assert len(stub.calls) == 2


def test_a_poll_that_never_ends_stops_at_twenty_minutes(world):
    stub = Stub([inv("InProgress", None, "")])
    with pytest.raises(ci.Refused, match="timed out waiting; the command may still be running"):
        world.go(stub)
    assert 1200 <= world.clock.now < 1210 and world.record() is None


@pytest.mark.parametrize("exit_code", range(2, 11))
def test_each_failure_exit_code_is_red_with_the_scripts_fixed_line(world, capsys, exit_code):
    line = sorted(ci.FAILURES)[exit_code - 2]
    stub = Stub([inv("Failed", exit_code, f"{line}\n{CANARY}\n", f"log tail with {CANARY}\n")])
    with pytest.raises(ci.Refused) as raised:
        world.go(stub)
    printed = capsys.readouterr().out + str(raised.value)
    assert line in printed and f"response code {exit_code}" in printed and "Failed" in printed
    assert CANARY not in printed
    assert world.record() is None and world.tries == []


@pytest.mark.parametrize("status", ["Failed", "TimedOut", "Cancelled", "Cancelling", "DeliveryTimedOut", "ExecutionTimedOut"])
def test_a_status_other_than_success_is_red(world, status):
    with pytest.raises(ci.Refused, match=status):
        world.go(Stub([inv(status, 1, "")]))
    assert world.record() is None


def test_success_with_a_nonzero_response_code_is_red(world):
    with pytest.raises(ci.Refused, match="response code 3"):
        world.go(Stub([inv("Success", 3)]))


def test_success_without_the_deployed_line_is_red(world):
    with pytest.raises(ci.Refused, match="did not report a deploy"):
        world.go(Stub([inv(out="nothing useful\n")]))
    assert world.record() is None


def test_a_line_that_only_looks_like_the_deployed_line_is_not_accepted(world):
    for out in ("socboard-deploy: deployed.!\n", "x socboard-deploy: deployed.\n", "socboard-deploy: Deployed.\n"):
        with pytest.raises(ci.Refused, match="did not report a deploy"):
            world.go(Stub([inv(out=out)]))


def test_nothing_but_the_fixed_lines_reaches_the_log(world, capsys):
    world.go(Stub([inv(out=f"{ci.DEPLOYED}\n{CANARY} from the server\n", err=f"stderr {CANARY}\n")]))
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    assert ci.DEPLOYED in captured.out


def test_the_environment_is_never_printed(world, capsys):
    world.go(Stub([inv()]))
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err


def test_a_health_check_that_fails_is_red_with_the_status_only_and_no_record(world, capsys):
    world.health = [503]
    with pytest.raises(ci.Refused, match=r"last: 503"):
        world.go(Stub([inv()]))
    assert len(world.tries) == 6 and world.record() is None
    assert world.clock.now == 50


def test_a_health_check_that_recovers_passes(world):
    world.health = [None, 502, 200]
    assert world.go(Stub([inv()])) == 0
    assert len(world.tries) == 3 and world.record() is not None


def test_a_redirect_is_not_a_pass(world):
    world.health = [301]
    with pytest.raises(ci.Refused):
        world.go(Stub([inv()]))


def test_no_answer_to_the_health_check_is_red(world):
    world.health = [None]
    with pytest.raises(ci.Refused, match="no answer"):
        world.go(Stub([inv()]))


@pytest.mark.parametrize(
    ("changes", "env_changes", "name"),
    [
        ({"digest": "latest"}, {}, "--digest"),
        ({"digest": DIGEST + "\n"}, {}, "--digest"),
        ({"digest": "sha256:" + "c" * 63}, {}, "--digest"),
        ({"digest": f"{DIGEST};id"}, {}, "--digest"),
        ({"tag": "latest"}, {}, "--tag"),
        ({"revision": "abc"}, {}, "--revision"),
        ({"image": "docker.io/library/postgres"}, {}, "--image"),
        ({"run_id": "12 34"}, {}, "--run-id"),
        ({}, {"DEPLOY_INSTANCE_ID": ""}, "DEPLOY_INSTANCE_ID is not set"),
        ({}, {"DEPLOY_INSTANCE_ID": None}, "DEPLOY_INSTANCE_ID is not set"),
        ({}, {"DEPLOY_INSTANCE_ID": "i-123"}, "DEPLOY_INSTANCE_ID is not an instance id"),
        ({}, {"DEPLOY_INSTANCE_ID": "I-0123456789ABCDEF0"}, "DEPLOY_INSTANCE_ID is not an instance id"),
        ({}, {"DEPLOY_INSTANCE_ID": INSTANCE + " --targets x"}, "DEPLOY_INSTANCE_ID is not an instance id"),
        ({}, {"DEPLOY_INSTANCE_ID": "i-0123456789abcdef012"}, "DEPLOY_INSTANCE_ID is not an instance id"),
        ({}, {"DEPLOY_URL": ""}, "DEPLOY_URL is not set"),
        ({}, {"DEPLOY_URL": None}, "DEPLOY_URL is not set"),
        ({}, {"DEPLOY_URL": "http://dev.example.test"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://dev.example.test/"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://dev.example.test/path"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://user@dev.example.test"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://dev.example.test?x=1"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://dev.example.test#x"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://dev.example.test:8443"}, "DEPLOY_URL is not https"),
        ({}, {"DEPLOY_URL": "https://"}, "DEPLOY_URL is not https"),
    ],
)
def test_a_bad_input_is_refused_before_any_aws_call_naming_the_variable_only(world, changes, env_changes, name):
    environment = {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL}
    for key, value in env_changes.items():
        if value is None:
            environment.pop(key)
        else:
            environment[key] = value
    stub = Stub([inv()])
    with pytest.raises(ci.Refused) as raised:
        world.go(stub, env=environment, **changes)
    assert name in str(raised.value)
    assert stub.calls == [] and world.record() is None
    for value in list(changes.values()) + list(env_changes.values()):
        if isinstance(value, str) and len(value) > 8:
            assert value not in str(raised.value)


def test_a_failed_send_names_the_stale_instance_id(world):
    with pytest.raises(ci.Refused, match="DEPLOY_INSTANCE_ID may be stale"):
        world.go(Stub([inv()], send_code=254))


@pytest.mark.parametrize("answer", ["", "not json", "{}", '{"Command": {}}', '{"Command": {"CommandId": 5}}', '{"Command": {"CommandId": "x; id"}}'])
def test_a_send_answer_without_a_good_command_id_is_refused(world, answer):
    stub = Stub([inv()], send=answer)
    with pytest.raises(ci.Refused):
        world.go(stub)
    assert len(stub.calls) == 1


def test_the_real_http_status_follows_no_redirect_and_keeps_no_body(monkeypatch):
    seen = {}

    class Response:
        status = 302

    class Connection:
        def __init__(self, host, timeout):
            seen["host"], seen["timeout"] = host, timeout

        def request(self, method, path, headers=None):
            seen["request"] = (method, path)

        def getresponse(self):
            return Response()

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(ci.http.client, "HTTPSConnection", Connection)
    assert ci.http_status("https://dev.example.test", 10) == 302
    assert seen == {"host": "dev.example.test", "timeout": 10, "request": ("GET", "/api/health"), "closed": True}


def test_http_status_gives_none_when_the_connection_fails(monkeypatch):
    def refuse(host, timeout):
        raise OSError("refused")

    monkeypatch.setattr(ci.http.client, "HTTPSConnection", refuse)
    assert ci.http_status("https://dev.example.test", 10) is None


def test_the_script_has_no_shell_and_calls_only_aws():
    text = (ROOT / "scripts" / "ci_deploy.py").read_text(encoding="utf-8")
    assert "shell=True" not in text and "os.system" not in text
    assert re.findall(r'\["aws"', text) == ['["aws"']


# --- the command line the workflow uses ----------------------------------------------------------


def test_find_writes_outputs_to_the_output_file_and_messages_to_stdout(tmp_path, capsys):
    path = tmp_path / "published-image.json"
    path.write_text(artifact(), encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("earlier=line\n", encoding="utf-8")
    argv = ["find", "--repository", REPOSITORY, "--head-sha", SHA, "--main-head", SHA, "--file", str(path), "--output", str(output)]
    assert ci.main(argv, {}) == 0
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "earlier=line" and lines[1] == "deploy=true"
    assert all("=" in line for line in lines)
    assert "deploy=" not in capsys.readouterr().out


def test_find_with_nothing_published_writes_only_deploy_false_to_the_output_file(tmp_path, capsys):
    output = tmp_path / "github_output"
    argv = ["find", "--repository", REPOSITORY, "--head-sha", SHA, "--main-head", SHA, "--file", "", "--output", str(output)]
    assert ci.main(argv, {}) == 0
    assert output.read_text(encoding="utf-8") == "deploy=false\n"
    assert "published nothing" in capsys.readouterr().out


def test_check_passes_good_variables_and_refuses_a_missing_one_with_exit_1(capsys):
    assert ci.main(["check"], {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL}) == 0
    assert ci.main(["check"], {"DEPLOY_URL": URL}) == 1
    assert "DEPLOY_INSTANCE_ID is not set" in capsys.readouterr().err


def test_deploy_from_the_command_line_refuses_with_exit_1_and_no_traceback(capsys):
    argv = ["deploy", "--digest", "latest", "--tag", TAG, "--revision", SHA, "--image", IMAGE, "--run-id", "1", "--run-url", "u", "--record", "r"]
    assert ci.main(argv, {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL}) == 1
    err = capsys.readouterr().err
    assert err.startswith("ci_deploy: --digest") and "Traceback" not in err
