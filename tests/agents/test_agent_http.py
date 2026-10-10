"""HTTP-level journey for the agent engine: authenticated owner, permissions,
tenant isolation, background run, download with hash, emergency stop.

Model responses are scripted (SIMULATED); all other layers are real.
"""
import hashlib
import json
import time

import pytest

from services.agents import AgentEngine
from services.audit.service import AuditService
from services.auth.auth_service import AuthenticationService, UserRole
from services.core.emergency_stop import EmergencyStopController
from services.tool_gateway.gateway import ToolGateway
from ui.server import JarvisServer
from tests.agents.scripted_provider import ScriptedModel
from tests.agents.test_agent_engine import OBJECTIVE, needs_netns
from tests.hardening.test_remediation import request


@pytest.fixture
def stack(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "admin2", "Second Admin", "AdminPassword123!", UserRole.ADMINISTRATOR)
    auth.create_user(root["user_id"], "viewer", "Viewer", "ViewerPassword123!", UserRole.VIEWER)
    audit = AuditService(db_path=tmp_path / "audit.db")
    gateway = ToolGateway(audit_service=audit, workspace_root=tmp_path)
    engine = AgentEngine(tmp_path / "engine", invoke=ScriptedModel(), allow_simulated=True,
                         stop_latch=gateway.stop_latch)
    stop = EmergencyStopController(gateway, audit)
    stop.attach("agent_engine", engine.halt_all)
    server = JarvisServer(port=0, auth_service=auth, emergency_stop=stop, agent_engine=engine)
    server.start()
    base = "http://127.0.0.1:" + str(server.httpd.server_address[1])
    cookies = {name: "hood_session=" + auth.authenticate(name, pw).session_token for name, pw in
               (("owner", "OwnerPassword123!"), ("admin2", "AdminPassword123!"), ("viewer", "ViewerPassword123!"))}
    try:
        yield base, cookies, engine, stop
    finally:
        server.stop()


def _json(resp):
    return json.loads(resp[1]) if resp[1] else None


@needs_netns
def test_owner_http_journey_to_verified_download(stack):
    base, c, engine, _ = stack
    assert request(base, "/api/agents/missions", c["owner"], {"objective": OBJECTIVE})[0] == 400  # no confirm
    status, body, _ = request(base, "/api/agents/missions", c["owner"],
                              {"objective": OBJECTIVE, "budget_usd": 1, "confirm": True})
    assert status == 201
    mission = json.loads(body)
    mid = mission["mission_id"]
    assert mission["state"] == "AWAITING_PLAN_APPROVAL"

    # Other tenants: not visible, not controllable.
    assert request(base, f"/api/agents/missions/{mid}", c["admin2"])[0] == 404
    assert request(base, f"/api/agents/missions/{mid}/approve", c["admin2"],
                   {"confirm": True, "plan_sha256": mission["plan_sha256"]})[0] == 404
    assert _json(request(base, "/api/agents/missions", c["admin2"])) == []
    # Viewers cannot start or read missions at all.
    assert request(base, "/api/agents/missions", c["viewer"], {"objective": OBJECTIVE, "confirm": True})[0] == 403
    assert request(base, f"/api/agents/missions/{mid}", c["viewer"])[0] == 403
    # CSRF still applies.
    assert request(base, f"/api/agents/missions/{mid}/approve", c["owner"],
                   {"confirm": True, "plan_sha256": mission["plan_sha256"]}, csrf=False)[0] == 403

    assert request(base, f"/api/agents/missions/{mid}/run", c["owner"], {"confirm": True})[0] == 409  # unapproved
    assert request(base, f"/api/agents/missions/{mid}/approve", c["owner"],
                   {"confirm": True, "plan_sha256": mission["plan_sha256"]})[0] == 200
    assert request(base, f"/api/agents/missions/{mid}/run", c["owner"], {"confirm": True})[0] == 202

    deadline = time.time() + 120
    while time.time() < deadline:
        current = _json(request(base, f"/api/agents/missions/{mid}", c["owner"]))
        if current["state"] not in ("QUEUED", "RUNNING", "VERIFYING") and not current["background_run_active"]:
            break
        time.sleep(0.3)
    assert current["state"] == "COMPLETED", current["error"]
    assert current["simulated"] is True

    status, data, headers = request(base, f"/api/agents/missions/{mid}/artifact", c["owner"])
    assert status == 200 and headers["Content-Type"] == "application/zip"
    assert hashlib.sha256(data).hexdigest() == headers["X-Content-SHA256"] == current["artifact"]["sha256"]
    assert request(base, f"/api/agents/missions/{mid}/artifact", c["admin2"])[0] == 404
    events = _json(request(base, f"/api/agents/missions/{mid}/events", c["owner"]))
    assert any(e["kind"] == "STATE" and "COMPLETED" in e["detail"] for e in events)


def test_emergency_stop_over_http_blocks_new_missions(stack):
    base, c, engine, stop = stack
    status, body, _ = request(base, "/api/emergency_stop", None, {})
    assert status == 200 and "agent_engine" in json.loads(body)["subsystems"]
    status, body, _ = request(base, "/api/agents/missions", c["owner"],
                              {"objective": OBJECTIVE, "budget_usd": 1, "confirm": True})
    assert status == 423 and "Emergency stop" in json.loads(body)["error"]


def test_invalid_mission_ids_are_not_found(stack):
    base, c, _, _ = stack
    for bad in ("../../etc", "agm_xyz", "msn_" + "0" * 32, "agm_" + "0" * 32):
        assert request(base, f"/api/agents/missions/{bad}", c["owner"])[0] == 404
