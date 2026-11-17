"""scripts/observability_up.py (#293): what `make observability-up` and `-down` run, with Docker faked.

Nothing here starts a container. The runner is a fake that records each command.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "observability_up.py"
spec = importlib.util.spec_from_file_location("observability_up", SCRIPT)
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

ROOT = Path(__file__).resolve().parent.parent


class Runner:
    def __init__(self, codes=None):
        self.commands, self.codes = [], list(codes or [])

    def __call__(self, command):
        self.commands.append(list(command))
        return self.codes.pop(0) if self.codes else 0


def test_up_makes_both_secrets_then_starts_both_compose_files_and_waits(capsys):
    runner = Runner()
    assert up.main(["up"], which=lambda name: "/usr/bin/docker", run=runner, environ={}) == 0
    assert runner.commands == [
        [sys.executable, "scripts/compose_secrets.py"],
        [sys.executable, "scripts/observability_secrets.py"],
        ["docker", "compose", "--env-file", "compose.secrets", "--env-file", "observability.secrets", "-f", "compose.yaml", "-f", "compose.observability.yaml", "up", "--build", "--wait", "--wait-timeout", "300"],
    ]
    out = capsys.readouterr().out
    assert "http://127.0.0.1:3000/" in out and "observability.secrets" in out


def test_the_grafana_address_follows_the_port_variable(capsys):
    up.main(["up"], which=lambda name: "x", run=Runner(), environ={"STACK_GRAFANA_PORT": "13000"})
    assert "http://127.0.0.1:13000/" in capsys.readouterr().out


def secrets_folder(tmp_path):
    for name in ("compose.secrets", "observability.secrets"):
        (tmp_path / name).write_text("", encoding="utf-8")
    return tmp_path


def test_down_removes_the_containers_and_keeps_the_volumes(tmp_path):
    runner = Runner()
    assert up.main(["down"], which=lambda name: "x", run=runner, root=secrets_folder(tmp_path)) == 0
    assert runner.commands == [["docker", "compose", "--env-file", "compose.secrets", "--env-file", "observability.secrets", "-f", "compose.yaml", "-f", "compose.observability.yaml", "down"]]
    assert "--volumes" not in runner.commands[0] and "-v" not in runner.commands[0]


@pytest.mark.parametrize("missing", ["compose.secrets", "observability.secrets"])
def test_down_without_a_secrets_file_names_it_and_runs_nothing(missing, tmp_path, capsys):
    folder = secrets_folder(tmp_path)
    (folder / missing).unlink()
    runner = Runner()
    assert up.main(["down"], which=lambda name: "x", run=runner, root=folder) == 2
    assert runner.commands == [] and f"{missing} is missing" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["up", "down"])
def test_without_docker_it_fails_with_the_reason_and_starts_nothing(action, capsys):
    runner = Runner()
    assert up.main([action], which=lambda name: None, run=runner) == 2
    assert runner.commands == []
    assert "Docker is not installed or not on the PATH; nothing was started" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["scripts/compose_secrets.py", "scripts/observability_secrets.py"])
def test_without_a_secrets_script_it_fails_naming_it_and_starts_nothing(missing, tmp_path, capsys):
    for script in up.SECRET_SCRIPTS:
        if script != missing:
            (tmp_path / script).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / script).write_text("", encoding="utf-8")
    runner = Runner()
    assert up.main(["up"], which=lambda name: "x", run=runner, root=tmp_path) == 2
    assert runner.commands == []
    assert f"{missing} is missing" in capsys.readouterr().err


def test_a_failing_secrets_script_stops_before_compose_and_keeps_its_exit_code(capsys):
    runner = Runner(codes=[0, 2])
    assert up.main(["up"], which=lambda name: "x", run=runner) == 2
    assert len(runner.commands) == 2 and not any(c[:2] == ["docker", "compose"] for c in runner.commands)
    assert "scripts/observability_secrets.py failed (exit 2); nothing was started" in capsys.readouterr().err


def test_a_failing_compose_up_returns_its_code_and_prints_no_grafana_address(capsys):
    assert up.main(["up"], which=lambda name: "x", run=Runner(codes=[0, 0, 1])) == 1
    assert "Grafana is on" not in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["start"], ["up", "down"], ["--volumes"]])
def test_a_bad_argument_is_refused_with_exit_2(args, capsys):
    runner = Runner()
    assert up.main(args, which=lambda name: "x", run=runner) == 2
    assert runner.commands == [] and "usage" in capsys.readouterr().err


def test_both_scripts_the_up_command_runs_exist():
    for script in up.SECRET_SCRIPTS:
        assert (ROOT / script).is_file(), script
