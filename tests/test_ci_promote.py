"""scripts/ci_promote.py (#269): the promote-to-prod workflow's logic, run against stub `gh` and `aws` commands.

Nothing here touches GitHub, AWS or the network. `subprocess.run` is replaced, so the script's real argument
lists are what the stub records. Every value is synthetic.
"""

import datetime
import importlib.util
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_promote", ROOT / "scripts" / "ci_promote.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)
dev = cp.dev

REPOSITORY = "Andrew-Chung-AU/socboard"
IMAGE = "ghcr.io/andrew-chung-au/socboard"
DIGEST_NEW = "sha256:" + "a" * 64
DIGEST_OLD = "sha256:" + "b" * 64
DIGEST_UNKNOWN = "sha256:" + "d" * 64
TAG_NEW = "20261116-030000-aaaaaaa"
TAG_OLD = "20261116-010000-bbbbbbb"
SHA_NEW = "a" * 40
SHA_OLD = "b" * 40
INSTANCE = "i-0123456789abcdef0"
URL = "https://prod.example.test"
COMMAND_ID = "11111111-2222-3333-4444-555555555555"
RUN_URL = "https://github.com/o/r/actions/runs/123456"
CANARY = "CANARY-secret-value-4f9c"


def dev_record(**changes):
    data = {
        "schema": 1, "environment": "dev", "result": "deployed", "image": IMAGE, "digest": DIGEST_NEW, "tag": TAG_NEW,
        "revision": SHA_NEW, "deployed_at": "2026-11-16T03:00:10Z", "run_id": "111", "run_url": "https://github.com/o/r/actions/runs/111",
        "command_id": COMMAND_ID,
    }
    for key, value in changes.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    return data


def prod_record(**changes):
    data = dev_record(environment="prod", run_id="222", run_url="https://github.com/o/r/actions/runs/222")
    data.update({"requested_by": "maintainer", "approved_by": ["maintainer"]})
    for key, value in changes.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    return data


def zip_of(*files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files:
            archive.writestr(name, text)
    return buffer.getvalue()


REPO_ID = 1000


def entry(identifier, created, name="deploy-record-dev", expired=False, branch="main", head_repo=REPO_ID):
    run = {"id": identifier * 10, "head_branch": branch, "repository_id": REPO_ID, "head_repository_id": head_repo}
    return {"id": identifier, "name": name, "expired": expired, "created_at": created, "workflow_run": run}


class Stub:
    """A stand-in for `gh` and `aws`: canned answers, every call recorded."""

    def __init__(self, dev_artifacts=(), prod_artifacts=(), zips=None, approvals=None, invocations=(), send_code=0):
        self.calls = []
        self.dev_artifacts, self.prod_artifacts = list(dev_artifacts), list(prod_artifacts)
        self.zips = zips or {}
        self.approvals = approvals
        self.invocations = list(invocations)
        self.send_code = send_code

    def __call__(self, argv, **kwargs):
        assert kwargs.get("shell") is None
        self.calls.append(list(argv))
        assert len(self.calls) <= 1000, "the script kept calling (a poll with no limit)"
        if argv[0] == "aws":
            if argv[2] == "send-command":
                return subprocess.CompletedProcess(argv, self.send_code, json.dumps({"Command": {"CommandId": COMMAND_ID}}), "")
            answer = self.invocations.pop(0) if len(self.invocations) > 1 else self.invocations[0]
            return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")
        assert argv[0] == "gh" and argv[1] == "api"
        path = argv[2]
        if "/actions/artifacts?name=deploy-record-dev" in path:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"total_count": len(self.dev_artifacts), "artifacts": self.dev_artifacts}), "")
        if "/actions/artifacts?name=deploy-record-prod" in path:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"total_count": len(self.prod_artifacts), "artifacts": self.prod_artifacts}), "")
        match = re.fullmatch(r"repos/[^/]+/[^/]+/actions/artifacts/(\d+)/zip", path)
        if match:
            body = self.zips[int(match.group(1))]
            return subprocess.CompletedProcess(argv, 0, body if not kwargs.get("text") else body.decode("utf-8"), "")
        if path.endswith("/approvals"):
            if self.approvals is None:
                return subprocess.CompletedProcess(argv, 1, "", "HTTP 403")
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.approvals), "")
        raise AssertionError(f"unexpected gh call {argv}")

    def gh_paths(self):
        return [call[2] for call in self.calls if call[0] == "gh"]


def two_records():
    """Dev ran OLD, then NEW (newest first in the list GitHub returns is not guaranteed, so the order here is scrambled)."""
    return Stub(
        dev_artifacts=[entry(5, "2026-11-16T01:00:00Z"), entry(9, "2026-11-16T03:00:00Z")],
        zips={
            5: zip_of(("deploy-record-dev.json", json.dumps(dev_record(digest=DIGEST_OLD, tag=TAG_OLD, revision=SHA_OLD, deployed_at="2026-11-16T01:00:10Z")))),
            9: zip_of(("deploy-record-dev.json", json.dumps(dev_record()))),
        },
    )


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = SimpleNamespace(stub=None, tmp=tmp_path, health=[200], tries=[])

    def fake_http(url, timeout):
        state.tries.append((url, timeout))
        return state.health.pop(0) if len(state.health) > 1 else state.health[0]

    monkeypatch.setattr(dev, "http_status", fake_http)

    def use(stub):
        state.stub = stub
        monkeypatch.setattr(subprocess, "run", stub)
        return stub

    def resolve(stub, digest="", env=None):
        use(stub)
        out = tmp_path / "out.txt"
        out.write_text("", encoding="utf-8")
        argv = ["resolve", "--repository", REPOSITORY, "--input-digest", digest, "--output", str(out)]
        code = cp.main(argv, {"GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")} if env is None else env)
        return code, out.read_text(encoding="utf-8")

    clock = SimpleNamespace(now=0.0)
    clock.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)

    def deploy(stub, env=None, **changes):
        use(stub)
        values = dict(repository=REPOSITORY, digest=DIGEST_NEW, tag=TAG_NEW, revision=SHA_NEW, image=IMAGE, previous=DIGEST_OLD,
                      run_id="123456", run_url=RUN_URL, record=str(tmp_path / "record.json"), summary=str(tmp_path / "deploy-summary.md"))
        values.update(changes)
        environment = {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL, "ACTOR": "maintainer", "AWS_SECRET_ACCESS_KEY": CANARY} if env is None else env
        when = datetime.datetime(2026, 11, 16, 4, 5, 6, tzinfo=datetime.timezone.utc)
        return cp.deploy(SimpleNamespace(**values), environment, clock=lambda: clock.now, sleep=clock.sleep, now=lambda: when)

    state.use, state.resolve, state.deploy = use, resolve, deploy
    state.record = lambda: json.loads((tmp_path / "record.json").read_text(encoding="utf-8")) if (tmp_path / "record.json").exists() else None
    return state


def success_invocation(out=dev.DEPLOYED + "\n"):
    return {"Status": "Success", "ResponseCode": 0, "StandardOutputContent": out, "StandardErrorContent": ""}


# --- resolve: which digest ----------------------------------------------------------------------


def test_with_no_input_the_digest_is_the_newest_dev_record(world):
    code, out = world.resolve(two_records())
    assert code == 0
    assert out.splitlines() == [f"image={IMAGE}", f"tag={TAG_NEW}", f"digest={DIGEST_NEW}", f"revision={SHA_NEW}", "previous=none"]


def test_the_newest_is_by_created_at_then_id_and_an_expired_artifact_is_skipped(world):
    stub = Stub(
        dev_artifacts=[entry(7, "2026-11-16T03:00:00Z", expired=True), entry(3, "2026-11-16T02:00:00Z"), entry(4, "2026-11-16T02:00:00Z")],
        zips={
            3: zip_of(("r.json", json.dumps(dev_record(digest=DIGEST_OLD, tag=TAG_OLD, revision=SHA_OLD)))),
            4: zip_of(("r.json", json.dumps(dev_record()))),
        },
    )
    code, out = world.resolve(stub)
    assert code == 0 and f"digest={DIGEST_NEW}" in out.splitlines()
    assert not any("/7/" in path for path in stub.gh_paths())


def test_an_input_that_dev_ran_is_promoted_with_the_records_own_tag_and_revision(world):
    code, out = world.resolve(two_records(), digest=DIGEST_OLD)
    assert code == 0
    assert out.splitlines() == [f"image={IMAGE}", f"tag={TAG_OLD}", f"digest={DIGEST_OLD}", f"revision={SHA_OLD}", "previous=none"]


def test_a_digest_that_never_ran_in_dev_is_refused_without_naming_it(world, capsys):
    code, out = world.resolve(two_records(), digest=DIGEST_UNKNOWN)
    assert code == 1 and out == ""
    captured = capsys.readouterr()
    assert "no dev deploy record holds this digest" in captured.err
    assert DIGEST_UNKNOWN not in captured.out + captured.err


@pytest.mark.parametrize("text", ["latest", "sha256:" + "A" * 64, "sha256:" + "a" * 63, " " + DIGEST_NEW, DIGEST_NEW + "\n", "v1.2.3", "a" * 64, "sha256:" + "g" * 64])
def test_an_input_that_is_not_a_digest_is_refused_before_any_gh_call(world, capsys, text):
    stub = two_records()
    code, out = world.resolve(stub, digest=text)
    assert code == 1 and out == "" and stub.calls == []
    captured = capsys.readouterr()
    assert text.strip() not in captured.out + captured.err


def test_no_dev_record_at_all_says_to_let_a_push_deploy_to_dev(world, capsys):
    code, out = world.resolve(Stub())
    assert code == 1 and out == ""
    assert "let a push to main deploy to dev first" in capsys.readouterr().err


def test_only_expired_records_count_as_none(world, capsys):
    code, _ = world.resolve(Stub(dev_artifacts=[entry(1, "2026-11-16T01:00:00Z", expired=True)]))
    assert code == 1 and "no dev deploy record" in capsys.readouterr().err


def test_a_record_uploaded_by_a_run_on_another_branch_is_ignored(world):
    # Newer and well formed, but left by a run on another branch: it must not be promoted or downloaded.
    stub = two_records()
    stub.dev_artifacts.append(entry(12, "2026-11-16T05:00:00Z", branch="feature"))
    stub.dev_artifacts.append({"id": 13, "name": "deploy-record-dev", "expired": False, "created_at": "2026-11-16T05:00:00Z"})
    # A fork's branch named main (a pull request from a fork runs in this repository's list too).
    stub.dev_artifacts.append(entry(14, "2026-11-16T05:00:00Z", head_repo=2000))
    stub.dev_artifacts.append(entry(15, "2026-11-16T05:00:00Z", head_repo=None))
    for identifier in (12, 13, 14, 15):
        stub.zips[identifier] = zip_of(("r.json", json.dumps(dev_record(digest=DIGEST_UNKNOWN))))
    code, out = world.resolve(stub)
    assert code == 0 and f"digest={DIGEST_NEW}" in out.splitlines()
    assert not any(f"/{identifier}/" in path for path in stub.gh_paths() for identifier in (12, 13, 14, 15))
    code, out = world.resolve(stub, digest=DIGEST_UNKNOWN)
    assert code == 1 and out == ""


def test_a_gh_call_that_hangs_is_a_clean_refusal_naming_no_path(world, monkeypatch, capsys):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 60)

    monkeypatch.setattr(subprocess, "run", hang)
    assert cp.main(["resolve", "--repository", REPOSITORY], {}) == 1
    err = capsys.readouterr().err
    assert "did not answer" in err and REPOSITORY not in err


def test_an_artifact_of_another_name_is_ignored(world, capsys):
    code, _ = world.resolve(Stub(dev_artifacts=[entry(1, "2026-11-16T01:00:00Z", name="something-else")]))
    assert code == 1


BAD_RECORDS = [
    ("environment", dict(environment="prod")),
    ("result", dict(result="failed")),
    ("image", dict(image="ghcr.io/someone-else/socboard")),
    ("digest", dict(digest="sha256:short")),
    ("tag", dict(tag="latest")),
    ("revision", dict(revision="c" * 39)),
    ("run_id", dict(run_id="abc")),
    ("run_url", dict(run_url="https://example.test/x")),
    ("deployed_at", dict(deployed_at="yesterday")),
    ("keys", dict(extra="x")),
    ("keys", dict(command_id=None)),
    ("schema", dict(schema=2)),
    ("schema", dict(schema=True)),
]


@pytest.mark.parametrize(("field", "changes"), BAD_RECORDS)
def test_a_newest_record_that_fails_its_check_is_refused_and_an_older_good_one_is_not_used(world, capsys, field, changes):
    stub = Stub(
        dev_artifacts=[entry(1, "2026-11-16T01:00:00Z"), entry(2, "2026-11-16T02:00:00Z")],
        zips={1: zip_of(("r.json", json.dumps(dev_record(digest=DIGEST_OLD, tag=TAG_OLD, revision=SHA_OLD)))), 2: zip_of(("r.json", json.dumps(dev_record(**changes))))},
    )
    code, out = world.resolve(stub)
    assert code == 1 and out == ""
    err = capsys.readouterr().err
    assert field in err and CANARY not in err


def test_a_bad_record_does_not_hide_a_good_one_that_the_input_names(world):
    stub = Stub(
        dev_artifacts=[entry(1, "2026-11-16T01:00:00Z"), entry(2, "2026-11-16T02:00:00Z")],
        zips={1: zip_of(("r.json", json.dumps(dev_record(digest=DIGEST_OLD, tag=TAG_OLD, revision=SHA_OLD)))), 2: zip_of(("r.json", "not json"))},
    )
    code, out = world.resolve(stub, digest=DIGEST_OLD)
    assert code == 0 and f"digest={DIGEST_OLD}" in out.splitlines()


@pytest.mark.parametrize("archive", [b"not a zip", zip_of(), zip_of(("a.json", "{}"), ("b.json", "{}")), zip_of(("big.json", " " * (cp.MAX_RECORD_BYTES + 1)))])
def test_an_artifact_that_is_not_one_small_file_is_refused(world, archive):
    stub = Stub(dev_artifacts=[entry(1, "2026-11-16T01:00:00Z")], zips={1: archive})
    code, out = world.resolve(stub)
    assert code == 1 and out == ""


@pytest.mark.parametrize("answer", ["not json", json.dumps({}), json.dumps({"artifacts": [{"id": True, "name": "deploy-record-dev", "expired": False, "created_at": "2026-11-16T01:00:00Z"}]}),
                                    json.dumps({"artifacts": [{"id": 1, "name": "deploy-record-dev", "expired": False, "created_at": "later"}]})])
def test_an_artifact_list_in_the_wrong_shape_is_refused(world, monkeypatch, answer):
    world.use(Stub())
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, answer, ""))
    assert cp.main(["resolve", "--repository", REPOSITORY, "--input-digest", ""], {}) == 1


def test_a_failed_gh_call_is_a_clean_refusal(world, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "HTTP 500"))
    assert cp.main(["resolve", "--repository", REPOSITORY], {}) == 1
    assert "could not be read" in capsys.readouterr().err


def test_a_missing_gh_command_line_is_a_clean_refusal(world, monkeypatch, capsys):
    def missing(argv, **kw):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)
    assert cp.main(["resolve", "--repository", REPOSITORY], {}) == 1
    assert "gh command line is not installed" in capsys.readouterr().err


def test_a_malformed_repository_is_refused_before_any_call(world, monkeypatch):
    stub = world.use(two_records())
    assert cp.main(["resolve", "--repository", "not a repo; rm -rf"], {}) == 1
    assert stub.calls == []


# --- resolve: the previous prod digest and the summary --------------------------------------------


def test_the_previous_prod_digest_is_the_newest_prod_record(world):
    stub = two_records()
    stub.prod_artifacts = [entry(20, "2026-11-16T03:30:00Z", name="deploy-record-prod")]
    stub.zips[20] = zip_of(("r.json", json.dumps(prod_record(digest=DIGEST_OLD))))
    _, out = world.resolve(stub)
    assert f"previous={DIGEST_OLD}" in out.splitlines()


def test_a_prod_record_that_cannot_be_read_gives_unknown_and_does_not_stop_the_run(world):
    stub = two_records()
    stub.prod_artifacts = [entry(20, "2026-11-16T03:30:00Z", name="deploy-record-prod")]
    stub.zips[20] = zip_of(("r.json", "not json"))
    code, out = world.resolve(stub)
    assert code == 0 and "previous=unknown" in out.splitlines()


def test_a_prod_record_with_a_bad_login_is_unknown(world):
    stub = two_records()
    stub.prod_artifacts = [entry(20, "2026-11-16T03:30:00Z", name="deploy-record-prod")]
    stub.zips[20] = zip_of(("r.json", json.dumps(prod_record(requested_by="bad login; x"))))
    _, out = world.resolve(stub)
    assert "previous=unknown" in out.splitlines()


def test_the_resolve_summary_names_the_choice_prod_now_and_the_recent_dev_digests(world):
    world.resolve(two_records(), digest=DIGEST_OLD)
    summary = (world.tmp / "summary.md").read_text(encoding="utf-8")
    assert f"`{DIGEST_OLD}` (named by the input)" in summary
    assert f"| `{DIGEST_NEW}` | `{TAG_NEW}` |" in summary and f"| `{DIGEST_OLD}` | `{TAG_OLD}` |" in summary
    assert summary.index(DIGEST_NEW + "` | `") < summary.index(DIGEST_OLD + "` | `")
    assert "Prod runs now | none" in summary


def test_resolve_prints_the_choice_and_writes_only_validated_values_to_the_output_file(world, capsys):
    _, out = world.resolve(two_records())
    for line in out.splitlines():
        key, _, value = line.partition("=")
        assert key in {"image", "tag", "digest", "revision", "previous"} and "\n" not in value and value == value.strip()
    assert "to promote: " + TAG_NEW in capsys.readouterr().out


# --- deploy ----------------------------------------------------------------------------------------


def test_a_promote_sends_the_digest_and_environment_prod_as_the_one_document(world):
    stub = Stub(invocations=[success_invocation()], approvals=[{"state": "approved", "user": {"login": "maintainer"}}])
    assert world.deploy(stub) == 0
    send = stub.calls[0]
    assert send[:3] == ["aws", "ssm", "send-command"]
    assert send[send.index("--document-name") + 1] == "socboard-deploy"
    assert json.loads(send[send.index("--parameters") + 1]) == {"digest": [DIGEST_NEW], "environment": ["prod"]}
    assert {call[2] for call in stub.calls if call[0] == "aws"} == {"send-command", "get-command-invocation"}
    forbidden = ("--targets", "--output-s3-bucket-name", "--output-s3-key-prefix", "--cli-input-json", "--document-version")
    assert not any(flag in call for call in stub.calls for flag in forbidden)


def test_a_passing_promote_writes_the_prod_record_with_exactly_its_keys(world):
    stub = Stub(invocations=[success_invocation()], approvals=[{"state": "approved", "user": {"login": "maintainer"}}, {"state": "rejected", "user": {"login": "other"}}])
    assert world.deploy(stub) == 0
    record = world.record()
    assert list(record) == list(cp.PROD_RECORD_KEYS)
    assert record == {
        "schema": 1, "environment": "prod", "result": "deployed", "image": IMAGE, "digest": DIGEST_NEW, "tag": TAG_NEW, "revision": SHA_NEW,
        "deployed_at": "2026-11-16T04:05:06Z", "run_id": "123456", "run_url": RUN_URL, "command_id": COMMAND_ID,
        "requested_by": "maintainer", "approved_by": ["maintainer"],
    }
    assert world.tries == [(URL, 10)]
    cp.check_record(json.dumps(record), REPOSITORY, "prod", cp.PROD_RECORD_KEYS)  # what the next resolve reads


def test_the_deploy_summary_names_the_digest_the_previous_one_who_started_and_who_approved(world):
    stub = Stub(invocations=[success_invocation()], approvals=[{"state": "approved", "user": {"login": "maintainer"}}])
    world.deploy(stub)
    summary = (world.tmp / "deploy-summary.md").read_text(encoding="utf-8")
    for expected in (f"`{DIGEST_NEW}`", f"`{DIGEST_OLD}`", "Started by | maintainer", "Approved by | maintainer", "2026-11-16T04:05:06Z", RUN_URL, "Health check | 200"):
        assert expected in summary


def test_already_deployed_is_a_pass_and_is_recorded_as_such(world):
    stub = Stub(invocations=[success_invocation(dev.ALREADY + "\n")], approvals=[])
    assert world.deploy(stub) == 0
    assert world.record()["result"] == "already deployed" and world.record()["approved_by"] == []


def test_an_approvals_call_that_fails_does_not_undo_a_deploy_that_happened(world):
    assert world.deploy(Stub(invocations=[success_invocation()], approvals=None)) == 0
    assert world.record()["approved_by"] == "unreadable"
    assert "not readable from GitHub" in (world.tmp / "deploy-summary.md").read_text(encoding="utf-8")


def test_an_approver_with_a_bad_login_or_a_wrong_state_is_left_out(world):
    approvals = [{"state": "approved", "user": {"login": "good-login"}}, {"state": "approved", "user": {"login": "bad login; x"}}, {"state": "pending", "user": {"login": "waiting"}},
                 {"state": "approved", "user": {"login": "good-login"}}]
    world.deploy(Stub(invocations=[success_invocation()], approvals=approvals))
    assert world.record()["approved_by"] == ["good-login"]


@pytest.mark.parametrize(("code", "line"), [(code, lines[0]) for code, lines in dev.FAILURE_LINES.items()])
def test_each_failure_of_the_script_fails_the_promote_and_leaves_no_record(world, capsys, code, line):
    stub = Stub(invocations=[{"Status": "Failed", "ResponseCode": code, "StandardOutputContent": line + "\n", "StandardErrorContent": ""}])
    with pytest.raises(cp.Refused):
        world.deploy(stub)
    assert world.record() is None
    assert line in capsys.readouterr().out


def test_a_health_check_that_never_answers_200_fails_and_leaves_no_record(world):
    world.health = [503]
    with pytest.raises(cp.Refused, match="public health check"):
        world.deploy(Stub(invocations=[success_invocation()], approvals=[]))
    assert world.record() is None


@pytest.mark.parametrize("actor", ["", "bad login; rm -rf", "x" * 40, "-lead", "lead-"])
def test_a_bad_actor_is_refused_before_any_aws_call(world, actor):
    stub = Stub(invocations=[success_invocation()])
    env = {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL, "ACTOR": actor}
    with pytest.raises(cp.Refused, match="ACTOR"):
        world.deploy(stub, env=env)
    assert stub.calls == []


@pytest.mark.parametrize("previous", ["", "latest", "sha256:short", DIGEST_OLD + "\n"])
def test_a_bad_previous_is_refused_before_any_aws_call(world, previous):
    stub = Stub(invocations=[success_invocation()])
    with pytest.raises(cp.Refused, match="--previous"):
        world.deploy(stub, previous=previous)
    assert stub.calls == []


@pytest.mark.parametrize("changes", [dict(digest="sha256:short"), dict(tag="latest"), dict(revision="x"), dict(image="docker.io/library/nginx"), dict(run_url="https://example.test")])
def test_a_bad_input_is_refused_before_any_aws_call(world, changes):
    stub = Stub(invocations=[success_invocation()])
    with pytest.raises(cp.Refused):
        world.deploy(stub, **changes)
    assert stub.calls == []


def test_the_environment_and_the_secrets_are_never_printed(world, capsys):
    world.deploy(Stub(invocations=[success_invocation()], approvals=[{"state": "approved", "user": {"login": "maintainer"}}]))
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err and INSTANCE not in captured.out + captured.err
    assert set(captured.out.splitlines()) <= {f"sent socboard-deploy for {TAG_NEW} to prod (command {COMMAND_ID})", dev.DEPLOYED, "health check 1 of 6: 200", f"prod is running {DIGEST_NEW} (deployed)"}


# --- ci_deploy.send keeps the dev call as it was ------------------------------------------------------


def test_a_dev_send_is_unchanged_and_a_prod_send_names_the_environment(monkeypatch):
    calls = []

    def stub(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({"Command": {"CommandId": COMMAND_ID}}), "")

    monkeypatch.setattr(subprocess, "run", stub)
    dev.send(INSTANCE, DIGEST_NEW, SHA_NEW)
    dev.send(INSTANCE, DIGEST_NEW, SHA_NEW, "dev")
    dev.send(INSTANCE, DIGEST_NEW, SHA_NEW, "prod")
    assert calls[0] == calls[1]
    assert calls[0][calls[0].index("--parameters") + 1] == f"digest={DIGEST_NEW}"
    assert json.loads(calls[2][calls[2].index("--parameters") + 1]) == {"digest": [DIGEST_NEW], "environment": ["prod"]}
    with pytest.raises(dev.Refused):
        dev.send(INSTANCE, DIGEST_NEW, SHA_NEW, "staging")


def test_the_script_runs_no_shell_and_calls_only_gh_and_aws():
    text = (ROOT / "scripts" / "ci_promote.py").read_text(encoding="utf-8")
    assert "shell=True" not in text and "os.system" not in text
    assert re.findall(r'\["gh"', text) == ['["gh"']
    assert '"aws"' not in text  # the aws calls are ci_deploy.py's, reused


def test_the_command_line_refuses_with_exit_1_and_no_traceback(world, capsys):
    argv = ["deploy", "--repository", REPOSITORY, "--digest", "sha256:short", "--tag", TAG_NEW, "--revision", SHA_NEW, "--image", IMAGE, "--previous", "none",
            "--run-id", "1", "--run-url", RUN_URL, "--record", str(world.tmp / "r.json")]
    assert cp.main(argv, {"DEPLOY_INSTANCE_ID": INSTANCE, "DEPLOY_URL": URL, "ACTOR": "maintainer"}) == 1
    captured = capsys.readouterr()
    assert "--digest is missing or malformed" in captured.err and "Traceback" not in captured.err
