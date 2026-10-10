"""Settings > Tools over HTTP: only the Root Owner allows installs, with explicit confirmation."""
import json
import time

import pytest

from services.auth.auth_service import AuthenticationService, UserRole
from services.toolbox import Toolbox
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request
from tests.toolbox.test_toolbox import FakeNet, php_present


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(Toolbox, "on_windows", lambda self: False)
    monkeypatch.setattr(Toolbox, "platform_problem", lambda self: None)
    routes.load_modules()
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "admin2", "Admin", "AdminPassword123!", UserRole.ADMINISTRATOR)
    server = JarvisServer(port=0, auth_service=auth)
    server.start()
    routes.SERVICES["toolbox"] = Toolbox(tmp_path / "data", runner=php_present, fetcher=FakeNet())
    base = "http://127.0.0.1:" + str(server.httpd.server_address[1])
    c = {n: "hood_session=" + auth.authenticate(n, pw).session_token
         for n, pw in (("owner", "OwnerPassword123!"), ("admin2", "AdminPassword123!"))}
    try:
        yield base, c
    finally:
        server.stop()
        routes.SERVICES.pop("toolbox", None)


def test_owner_allows_tools_once_and_sees_the_install(stack):
    base, c = stack
    status = json.loads(request(base, "/api/tools", c["owner"])[1])
    assert {t["id"] for t in status["tools"]} >= {"php", "wordpress", "wp_sqlite", "wp_cli"}
    need = json.loads(request(base, "/api/tools/needs?profile=wordpress_site", c["owner"])[1])
    assert need["unapproved"] == ["wordpress", "wp_sqlite", "wp_cli"] and not need["ready"]
    body = {"tools": ["wordpress", "wp_sqlite", "wp_cli"]}
    assert request(base, "/api/tools/install", c["admin2"], {**body, "confirm": True})[0] == 403
    assert request(base, "/api/tools/install", c["owner"], body)[0] == 400                    # no confirm
    assert request(base, "/api/tools/install", c["owner"], {**body, "confirm": True}, csrf=False)[0] == 403
    assert request(base, "/api/tools/install", c["owner"], {"tools": ["rm -rf"], "confirm": True})[0] == 400
    job = json.loads(request(base, "/api/tools/install", c["owner"], {**body, "confirm": True})[1])
    deadline = time.time() + 20
    while time.time() < deadline:
        j = json.loads(request(base, f"/api/tools/jobs/{job['id']}", c["owner"])[1])
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done", j
    assert json.loads(request(base, "/api/tools/needs?profile=wordpress_site", c["owner"])[1])["ready"]
    # Allowed once: updates don't need a new confirmation.
    assert request(base, "/api/tools/update", c["owner"], {"tool": "wp_cli"})[0] == 200


def test_one_approval_sets_up_the_sandbox_and_installs_the_tools(stack, tmp_path, monkeypatch):
    """Windows: "Allow & set up" covers HOOD's sandbox and the tools; restart only when Windows needs it."""
    from services.toolbox.wsl import WslSandbox
    from tests.toolbox.test_wsl_sandbox import WSL_EXE, FakeWindows, fetcher
    base, c = stack
    monkeypatch.undo()          # the fixture's Linux stand-in off: the real checks, on a faked Windows
    monkeypatch.setattr(WslSandbox, "wsl_exe", lambda self: WSL_EXE)
    monkeypatch.setattr(Toolbox, "on_windows", lambda self: True)
    win = FakeWindows(wsl_installed=False, needs_restart=True)
    sbx = WslSandbox(tmp_path / "w", runner=win, fetcher=fetcher())
    routes.SERVICES["wsl_sandbox"] = sbx
    routes.SERVICES["toolbox"] = Toolbox(tmp_path / "w", runner=win, wsl=sbx, fetcher=FakeNet())
    try:
        st = json.loads(request(base, "/api/sandbox", c["owner"])[1])
        assert st["managed"] and st["phase"] == "not_set_up" and len(st["approval_text"]) == 3
        need = json.loads(request(base, "/api/tools/needs?profile=wordpress_site", c["owner"])[1])
        assert need["sandbox"] == {"approved": False, "phase": "not_set_up", "ready": False,
                                   "approval_text": st["approval_text"], "last_error": None}
        body = {"tools": ["php"], "confirm": True}
        assert request(base, "/api/tools/install", c["owner"], body)[0] == 409       # sandbox not approved
        assert request(base, "/api/sandbox/setup", c["admin2"], {"confirm": True})[0] == 403
        assert request(base, "/api/sandbox/setup", c["owner"], {})[0] == 400
        assert request(base, "/api/sandbox/restart", c["owner"], {"confirm": True})[0] == 409   # not needed
        job = json.loads(request(base, "/api/tools/install", c["owner"], {**body, "with_sandbox": True})[1])
        assert job["sandbox_job"]["state"] == "running"
        sbx.wait()
        deadline = time.time() + 20
        while time.time() < deadline:
            j = json.loads(request(base, f"/api/tools/jobs/{job['id']}", c["owner"])[1])
            if j["state"] != "running":
                break
            time.sleep(0.1)
        assert j["state"] == "waiting", j
        st = json.loads(request(base, "/api/sandbox", c["owner"])[1])
        assert st["phase"] == "restart_needed" and "restart" in st["problem"]
        restart = json.loads(request(base, "/api/sandbox/restart", c["owner"], {"confirm": True})[1])
        assert "60 seconds" in restart["message"] and win.calls[-1][0] == "shutdown.exe"
    finally:
        routes.SERVICES.pop("wsl_sandbox", None)
