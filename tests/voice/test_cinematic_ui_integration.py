"""
Integration and End-to-End Tests for HOOD Cinematic Command Center UI
Verifying:
1. Cinematic HTML/CSS/JS delivery and asset truthfulness
2. Real-time telemetry endpoints (/api/telemetry, /api/economic/summary, /api/impossible_list, /api/intelligence/summary)
3. Project Sentinel Security Center endpoints (/api/sentinel/summary, /api/sentinel/findings, /api/sentinel/scan)
4. X Executive mode activation and stand-down with Root Owner authentication
5. Governance, approvals, and emergency stop
"""

import time
import json
import urllib.request
import urllib.parse
import pytest
from pathlib import Path

from ui.server import JarvisServer, HoodServer
from services.interaction.interaction_service import InteractionService, UIState
from services.core.emergency_stop import EmergencyStopController
from services.core.hood_commander import HoodCommander
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.memory.service import MemoryService
from services.tool_gateway.gateway import ToolGateway
from services.voice.voice_router import VoiceRouter
from packages.config import load_config
from services.sentinel.sentinel_service import SecuritySentinelService
from services.auth.auth_service import AuthenticationService


@pytest.fixture(scope="module")
def cinematic_server():
    cfg = load_config(Path("hood.config.yaml"))
    audit = AuditService(Path(cfg.storage.sqlite_path))
    memory = MemoryService(Path(cfg.storage.sqlite_path))
    approvals = ApprovalService()
    tools = ToolGateway(cfg, approvals, audit)
    voice = VoiceRouter(cfg)

    commander = HoodCommander(cfg, None, approvals, audit, memory)
    interaction = InteractionService(commander, approvals, memory, voice, cfg)
    emergency = EmergencyStopController(tools, audit)
    
    test_db = Path("artifacts/test_auth_cinematic.db")
    if test_db.exists():
        test_db.unlink()
    auth = AuthenticationService(db_path=test_db)
    auth.initialize_root_owner(username="zack", display_name="Zakaria", password="TestMasterPass123!")
    sess = auth.authenticate("zack", "TestMasterPass123!")

    sentinel = SecuritySentinelService(registry_path=Path("artifacts/test_sentinel_cinematic.json"))

    # Bind port 8997 for isolated test
    server = JarvisServer(
        interaction_service=interaction,
        emergency_stop=emergency,
        runtime=None,
        port=8997,
        auth_service=auth,
        sentinel_service=sentinel
    )
    server.start()
    time.sleep(0.3)

    yield {
        "server": server,
        "base_url": "http://127.0.0.1:8997",
        "interaction": interaction,
        "emergency": emergency,
        "sentinel": sentinel,
        "auth": auth,
        "session": sess
    }

    server.stop()


def test_cinematic_frontend_assets_served(cinematic_server):
    base_url = cinematic_server["base_url"]

    # 1. HTML index (classic cinematic console now lives at /classic;
    #    HOOD NEXT is the default at /).
    req = urllib.request.Request(f"{base_url}/classic")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "<title>HOOD</title>" in html
        assert "NOVA" in html and "DEVELOPMENT BUILD" in html
        assert "avatar-hero-container" in html
        assert "audio-visualizer" in html
        assert "btn-emergency-stop" in html

    # 2. CSS stylesheet
    req_css = urllib.request.Request(f"{base_url}/static/style.css")
    with urllib.request.urlopen(req_css) as resp_css:
        assert resp_css.status == 200
        css = resp_css.read().decode("utf-8")
        assert "theme-x" in css
        assert "gyro-ring" in css
        assert "audio-equalizer" in css

    # 3. JavaScript logic
    req_js = urllib.request.Request(f"{base_url}/static/app.js")
    with urllib.request.urlopen(req_js) as resp_js:
        assert resp_js.status == 200
        js = resp_js.read().decode("utf-8")
        assert "harmonicFrequencies" in js
        assert "updateXThemeState" in js
        assert "loadEconomicData" in js
        assert "loadImpossibleListData" in js


def test_real_telemetry_and_new_endpoints(cinematic_server):
    """Operational endpoints are private; authenticated data must not invent assurances."""
    base = cinematic_server["base_url"]
    token = cinematic_server["session"].session_token
    from urllib.error import HTTPError
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(f"{base}/api/telemetry")
    assert error.value.code == 401
    for path, keys in (
        ("/api/telemetry", ("desktop", "evolution", "voice", "nodes")),
        ("/api/economic/summary", ("mode", "pipeline", "rent_vs_own")),
        ("/api/impossible_list", ()),
        ("/api/intelligence/summary", ("levels", "frontier_gap")),
    ):
        req = urllib.request.Request(f"{base}{path}", headers={"Cookie": f"hood_session={token}"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read())
            for key in keys:
                assert key in data


def test_sentinel_and_x_red_team_flow(cinematic_server):
    """Owner can read Sentinel, but direct X activation must fail closed."""
    base = cinematic_server["base_url"]
    token = cinematic_server["session"].session_token
    req = urllib.request.Request(f"{base}/api/sentinel/summary",
                                 headers={"Cookie": f"hood_session={token}"})
    with urllib.request.urlopen(req) as resp:
        summary = json.loads(resp.read())
        assert "posture" in summary
        assert "firewall" in summary
        assert summary["x_red_team"]["status"] == "DORMANT"
    from urllib.error import HTTPError
    req = urllib.request.Request(
        f"{base}/api/sentinel/x/activate", data=json.dumps({"target": "ISOLATED_HOOD_SANDBOX"}).encode(),
        headers={"Content-Type": "application/json", "Cookie": f"hood_session={token}",
                 "X-CSRF-Token": cinematic_server["session"].csrf_token})
    with pytest.raises(HTTPError) as error:
        urllib.request.urlopen(req)
    assert error.value.code == 409
    assert cinematic_server["sentinel"].get_security_summary()["x_red_team"]["status"] == "DORMANT"


def test_emergency_stop_halts_system_cleanly(cinematic_server):
    base_url = cinematic_server["base_url"]
    emergency = cinematic_server["emergency"]
    interaction = cinematic_server["interaction"]

    # Trigger emergency stop via UI endpoint
    req = urllib.request.Request(
        f"{base_url}/api/emergency_stop",
        data=b"{}",
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert "EMERGENCY_STOP" in data["status"]
        assert emergency.is_active is True
        assert interaction.sessions[interaction.active_session_id].ui_state == UIState.EMERGENCY_STOP
