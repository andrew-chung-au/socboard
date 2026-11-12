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
