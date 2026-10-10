"""Website missions and "Run on my PC" over real HTTP: mission kind, live file view,
sandboxed preview (capability URL, no session, strict CSP), Settings > Agents and the
per-mission local-run approval. Model output is scripted (SIMULATED)."""
import json
import time
import urllib.request

import pytest

from services.agents import AgentEngine
from services.agents import engine as engine_mod
from services.auth.auth_service import AuthenticationService, UserRole
from ui import routes
from ui.server import JarvisServer
from tests.agents.scripted_provider import ScriptedModel
from tests.agents.scripted_web import WEB_OBJECTIVE, ScriptedWebModel
from tests.agents.test_agent_engine import OBJECTIVE
from tests.hardening.test_remediation import request


def _stack(tmp_path, model):
    routes.load_modules()
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "admin2", "Second Admin", "AdminPassword123!", UserRole.ADMINISTRATOR)
    engine = AgentEngine(tmp_path / "engine", invoke=model, allow_simulated=True)
    server = JarvisServer(port=0, auth_service=auth, agent_engine=engine)
    server.start()
    base = "http://127.0.0.1:" + str(server.httpd.server_address[1])
    cookies = {n: "hood_session=" + auth.authenticate(n, pw).session_token
               for n, pw in (("owner", "OwnerPassword123!"), ("admin2", "AdminPassword123!"))}
    return server, base, cookies, engine


@pytest.fixture
def web(tmp_path):
    server, base, cookies, engine = _stack(tmp_path, ScriptedWebModel())
    try:
        yield base, cookies, engine
    finally:
        server.stop()


def _j(resp):
    return json.loads(resp[1])


def _wait(base, cookie, mid, states, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = _j(request(base, f"/api/agents/missions/{mid}", cookie))
        if cur["state"] in states and not cur.get("background_run_active"):
            return cur
        time.sleep(0.2)
    raise AssertionError(f"mission stuck in {cur['state']}")


def test_website_mission_files_and_sandboxed_preview(web):
    base, c, engine = web
    created = _j(request(base, "/api/agents/missions", c["owner"],
                         {"objective": WEB_OBJECTIVE, "confirm": True, "profile": "static_web"}))
    mid = created["mission_id"]
    assert created["profile"] == "static_web"
    assert request(base, "/api/agents/missions", c["owner"],
                   {"objective": WEB_OBJECTIVE, "confirm": True, "profile": "rocket"})[0] == 400
    request(base, f"/api/agents/missions/{mid}/approve", c["owner"], {"confirm": True,
                                                                      "plan_sha256": created["plan_sha256"]})
    assert request(base, f"/api/agents/missions/{mid}/run", c["owner"], {"confirm": True})[0] == 202
    done = _wait(base, c["owner"], mid, {"COMPLETED", "FAILED", "UNVERIFIED", "BLOCKED"})
    assert done["state"] == "COMPLETED", done["error"]

    files = _j(request(base, f"/api/agents/missions/{mid}/files", c["owner"]))
    assert {"site/index.html", "qa_checks/acceptance.json"} <= {f["path"] for f in files["files"]}
    view = _j(request(base, f"/api/agents/missions/{mid}/file?path=site/menu.html", c["owner"]))
    assert "Cappuccino" in view["text"]
    assert request(base, f"/api/agents/missions/{mid}/file?path=../.receipt_key", c["owner"])[0] == 404
    assert request(base, f"/api/agents/missions/{mid}/files", c["admin2"])[0] == 404     # other tenant

    # Preview: no cookie needed, token required, sandboxed with no network and no same-origin.
    status, body, headers = request(base, done["preview_path"])
    assert status == 200 and b"Bean There" in body
    csp = headers["Content-Security-Policy"]
    assert csp.startswith("sandbox allow-scripts") and "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp and "form-action 'none'" in csp and f"/preview/{mid}/" in csp
    assert headers["Referrer-Policy"] == "no-referrer"
    bad = done["preview_path"].replace(done["preview_path"].split("/")[3], "0" * 40)
    assert request(base, bad)[0] == 404
    assert request(base, done["preview_path"].replace("index.html", "..%2F..%2F.receipt_key"))[0] == 404
    # The normal console keeps its strict policy.
    assert "sandbox" not in request(base, "/api/auth/status")[2]["Content-Security-Policy"]


def test_preview_refuses_foreign_host(web):
    base, c, engine = web
    req = urllib.request.Request(base + "/preview/agm_" + "0" * 32 + "/x/index.html", headers={"Host": "evil.test"})
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("expected 421")
    except urllib.error.HTTPError as exc:
        assert exc.code == 421


def test_run_on_my_pc_needs_setting_then_per_mission_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "sandbox_problem", lambda *a: "Process sandbox is only implemented for POSIX hosts")
    server, base, c, engine = _stack(tmp_path, ScriptedModel())
    try:
        settings = _j(request(base, "/api/settings/agents", c["owner"]))
        assert settings["allow_local_run"] is False and settings["sandbox_available"] is False
        assert request(base, "/api/settings/agents", c["admin2"])[0] == 403
        created = _j(request(base, "/api/agents/missions", c["owner"], {"objective": OBJECTIVE, "confirm": True}))
        mid = created["mission_id"]
        request(base, f"/api/agents/missions/{mid}/approve", c["owner"],
                {"confirm": True, "plan_sha256": created["plan_sha256"]})
        request(base, f"/api/agents/missions/{mid}/run", c["owner"], {"confirm": True})
        waiting = _wait(base, c["owner"], mid, {"BLOCKED", "COMPLETED", "FAILED", "UNVERIFIED"})
        assert waiting["state"] == "BLOCKED" and waiting["local_run"]["awaiting_approval"]
        sha = waiting["local_run"]["workspace_sha256"]
        url = f"/api/agents/missions/{mid}/approve_local_run"
        assert request(base, url, c["owner"], {"confirm": True, "workspace_sha256": sha})[0] == 409   # setting off
        assert request(base, "/api/settings/agents", c["owner"], {"allow_local_run": True})[0] == 400  # no confirm
        on = _j(request(base, "/api/settings/agents", c["owner"], {"allow_local_run": True, "confirm": True}))
        assert on["allow_local_run"] is True and on["changed_by"] == "owner"
        assert request(base, url, c["admin2"], {"confirm": True, "workspace_sha256": sha})[0] == 403
        assert request(base, url, c["owner"], {"workspace_sha256": sha})[0] == 400                    # no confirm
        assert request(base, url, c["owner"], {"confirm": True, "workspace_sha256": sha}, csrf=False)[0] == 403
        ok = request(base, url, c["owner"], {"confirm": True, "workspace_sha256": sha})
        assert ok[0] == 200 and _j(ok)["state"] == "VERIFYING"
    finally:
        server.stop()
