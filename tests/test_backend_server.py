import pytest

from socboard.server import DEFAULT_HOST, DEFAULT_PORT, BindError, main, resolve_bind


def test_default_bind_is_localhost():
    assert resolve_bind({}) == (DEFAULT_HOST, DEFAULT_PORT)
    assert DEFAULT_HOST == "127.0.0.1"


def test_host_and_port_come_from_the_environment():
    assert resolve_bind({"SOCBOARD_HOST": "localhost", "SOCBOARD_PORT": "8123"}) == (
        "localhost",
        8123,
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "example.com", ""])
def test_non_localhost_host_is_refused(host):
    with pytest.raises(BindError):
        resolve_bind({"SOCBOARD_HOST": host})


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", "", "80.5", "８０"])
def test_bad_port_is_refused(port):
    with pytest.raises(BindError):
        resolve_bind({"SOCBOARD_PORT": port})


def test_main_refuses_a_public_host_before_starting_anything(capsys):
    assert main({"SOCBOARD_HOST": "0.0.0.0"}) == 2
    assert "localhost" in capsys.readouterr().err


# --- --allow-non-loopback-host (#142) ---------------------------------------------------------

FLAG = "--allow-non-loopback-host"
USAGE = "usage: python -m socboard.server [--allow-non-loopback-host]"
GOOD_ENV = {
    "SOCBOARD_TOKEN_SECRET": "test-only-signing-secret-0123456789abcdef",
    "SOCBOARD_DATABASE_URL": "sqlite:///:memory:",
}


@pytest.fixture
def runs(monkeypatch):
    """Replace the uvicorn run call: a test never starts a server."""
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    return calls


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5"])
def test_main_exits_2_for_a_non_loopback_address_without_the_flag(host, capsys, runs):
    assert main({**GOOD_ENV, "SOCBOARD_HOST": host}) == 2
    err = capsys.readouterr().err
    assert "SOCBOARD_HOST must be localhost: 127.0.0.1, localhost or ::1" in err
    assert host not in err
    assert runs == []


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "2001:db8::1"])
def test_flag_accepts_an_ip_address(host):
    assert resolve_bind({"SOCBOARD_HOST": host}, allow_non_loopback=True) == (host, DEFAULT_PORT)


def test_main_with_the_flag_starts_on_a_non_loopback_address(runs):
    assert main({**GOOD_ENV, "SOCBOARD_HOST": "0.0.0.0", "SOCBOARD_PORT": "8123"}, [FLAG]) == 0
    assert [(call["host"], call["port"]) for call in runs] == [("0.0.0.0", 8123)]


@pytest.mark.parametrize(
    "host",
    ["example.com", "", "   ", "http://0.0.0.0", "0.0.0.0:8000", "0.0.0.0/0", "fe80::1%eth0", "[::]"],
)
def test_flag_still_refuses_a_value_that_is_not_an_address(host, capsys, runs):
    with pytest.raises(BindError):
        resolve_bind({"SOCBOARD_HOST": host}, allow_non_loopback=True)
    assert main({**GOOD_ENV, "SOCBOARD_HOST": host}, [FLAG]) == 2
    err = capsys.readouterr().err
    assert "SOCBOARD_HOST" in err and FLAG in err
    assert host.strip() == "" or host not in err
    assert runs == []


def test_no_environment_variable_enables_it(capsys, runs):
    # Every SOCBOARD_* name the repository reads, plus guesses at an opt-in name.
    environ = {
        **GOOD_ENV,
        "SOCBOARD_HOST": "0.0.0.0",
        "SOCBOARD_PORT": "8000",
        "SOCBOARD_INGEST_API_KEY": "x" * 20,
        "SOCBOARD_ALLOW_NON_LOOPBACK_HOST": "1",
        "SOCBOARD_ALLOW_NON_LOOPBACK": "true",
        "SOCBOARD_ALLOW_PUBLIC_BIND": "1",
        "SOCBOARD_CONTAINER": "1",
        "SOCBOARD_BIND_ALL": "1",
    }
    with pytest.raises(BindError):
        resolve_bind(environ)
    assert main(environ) == 2
    assert "SOCBOARD_HOST must be localhost" in capsys.readouterr().err
    assert runs == []


def test_refusal_messages_name_the_setting_and_no_value(capsys):
    secret = "test-only-signing-secret-0123456789abcdef"
    for flags, host in (([], "10.9.8.7"), ([FLAG], "not-an-address-7")):
        assert main({**GOOD_ENV, "SOCBOARD_HOST": host}, flags) == 2
        err = capsys.readouterr().err
        assert "SOCBOARD_HOST" in err
        assert host not in err and secret not in err


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", "", "80.5", "８０"])
def test_flag_leaves_the_port_rules_unchanged(port, capsys, runs):
    with pytest.raises(BindError):
        resolve_bind({"SOCBOARD_HOST": "0.0.0.0", "SOCBOARD_PORT": port}, allow_non_loopback=True)
    assert main({**GOOD_ENV, "SOCBOARD_HOST": "0.0.0.0", "SOCBOARD_PORT": port}, [FLAG]) == 2
    assert "SOCBOARD_PORT" in capsys.readouterr().err
    assert runs == []


@pytest.mark.parametrize("host", [None, "127.0.0.1", "localhost", "::1"])
def test_flag_with_a_loopback_host_behaves_as_without(host, runs):
    environ = {"SOCBOARD_PORT": "8123"} | ({} if host is None else {"SOCBOARD_HOST": host})
    assert resolve_bind(environ, allow_non_loopback=True) == resolve_bind(environ)
    assert main({**GOOD_ENV, **environ}, [FLAG]) == 0
    assert main({**GOOD_ENV, **environ}) == 0
    assert runs[0] == runs[1]


@pytest.mark.parametrize(
    "argv",
    [
        ["--bogus"],
        ["bogus"],
        [FLAG, "--bogus"],
        [f"{FLAG}=1"],
        [f"{FLAG}=0.0.0.0"],
        [FLAG, "0.0.0.0"],
        ["--allow"],
        ["--allow-non-loopback"],
        ["-h"],
        ["--help"],
    ],
)
def test_unknown_argument_or_a_value_for_the_flag_exits_2_and_starts_nothing(argv, capsys, runs):
    assert main({**GOOD_ENV, "SOCBOARD_HOST": "0.0.0.0"}, argv) == 2
    err = capsys.readouterr().err
    assert err.startswith(USAGE)
    assert "0.0.0.0" not in err
    assert runs == []
