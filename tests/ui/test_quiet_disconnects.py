"""A browser dropping a connection mid-response must not flood the console
(owner's Windows run: WinError 10053 tracebacks), but real errors still print."""
import pytest

from ui.server import _QuietDisconnectServer


@pytest.fixture
def server():
    srv = _QuietDisconnectServer(("127.0.0.1", 0), object)
    yield srv
    srv.server_close()


@pytest.mark.parametrize("exc", [ConnectionAbortedError(10053, "aborted"), BrokenPipeError(), ConnectionResetError()])
def test_client_disconnect_is_silent(server, capsys, exc):
    try:
        raise exc
    except ConnectionError:
        server.handle_error(None, ("127.0.0.1", 1))
    assert capsys.readouterr().err == ""


def test_real_errors_are_still_reported(server, capsys):
    try:
        raise ValueError("boom")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 1))
    assert "ValueError: boom" in capsys.readouterr().err
