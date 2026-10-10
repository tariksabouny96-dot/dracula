"""Self-repair and the Intelligence page's live data over HTTP: Root Owner only, explicit
confirmation for every step that sends code to the AI provider or changes HOOD, applying bound to
the exact fix shown, and live numbers that come from HOOD's own stores (never invented)."""
import base64
import json

import pytest

from packages.config import BudgetSettings, SystemConfig
from packages.config.pricing import ModelPrice
from packages.contracts import ModelUsage
from services.agents import sandbox as sandbox_mod
from services.auth.auth_service import AuthenticationService, UserRole
from services.model_gateway.cost_controller import CostController
from services.model_gateway.router import ModelRouter
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request
from tests.selfrepair_service.test_self_repair import BUGGY, REPORT, Model, proposal, repo, service  # noqa: F401

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()


@pytest.fixture
def server(tmp_path, monkeypatch, repo):  # noqa: F811
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(sandbox_mod, "sandbox_problem", lambda *a: "no sandbox on this computer")
    routes.load_modules()
    allowed = {"on": True}
    svc = service(tmp_path, repo, Model(proposal(), proposal()), local_run_allowed=lambda: allowed["on"])
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "admin2", "Admin", "AdminPassword123!", UserRole.ADMINISTRATOR)
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    routes.SERVICES["selfrepair"] = svc          # after start(): start() resets SERVICES from the runtime
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
    admin = "hood_session=" + auth.authenticate("admin2", "AdminPassword123!").session_token
    try:
        yield base, owner, admin, svc, repo
    finally:
        srv.stop()
        for name in ("selfrepair", "router"):
            routes.SERVICES.pop(name, None)


def test_only_the_root_owner_reaches_self_repair(server):
    base, owner, admin, svc, repo = server
    assert request(base, "/api/selfrepair")[0] == 401
    assert request(base, "/api/selfrepair", admin)[0] == 403
    assert request(base, "/api/selfrepair/report", admin, {"text": REPORT, "confirm": True})[0] == 403
    assert request(base, "/api/selfrepair", owner)[0] == 200
    assert request(base, "/api/selfrepair/sr_000000000000", owner)[0] == 404


def test_report_needs_confirmation_then_fix_is_checked_applied_and_undone(server):
    base, owner, admin, svc, repo = server
    assert request(base, "/api/selfrepair/report", owner, {"text": REPORT})[0] == 400      # no confirm
    assert svc.list() == []                                                            # nothing sent
    status, body, _ = request(base, "/api/selfrepair/report", owner, {"text": REPORT, "confirm": True})
    assert status == 201, body
    rid = json.loads(body)["id"]
    r = json.loads(request(base, f"/api/selfrepair/{rid}", owner)[1])
    assert r["state"] == "AWAITING_LOCAL_RUN"                       # no sandbox here: the owner decides
    assert request(base, f"/api/selfrepair/{rid}/apply", owner,
                   {"proposal_sha": r["proposal_sha"], "confirm": True})[0] == 409   # not proven yet
    assert request(base, f"/api/selfrepair/{rid}/run_checks_here", owner, {})[0] == 400  # no confirm
    status, body, _ = request(base, f"/api/selfrepair/{rid}/run_checks_here", owner, {"confirm": True})
    r = json.loads(body)
    assert status == 200 and r["state"] == "NEEDS_DECISION", (r.get("error"), r.get("checks"))
    assert (repo / "services/demo/greeting.py").read_text() == BUGGY                 # still unchanged
    assert request(base, f"/api/selfrepair/{rid}/apply", owner, {"proposal_sha": "0" * 64, "confirm": True})[0] == 409
    assert request(base, f"/api/selfrepair/{rid}/apply", owner, {"proposal_sha": r["proposal_sha"]})[0] == 400
    status, body, _ = request(base, f"/api/selfrepair/{rid}/apply", owner,
                              {"proposal_sha": r["proposal_sha"], "confirm": True})
    assert status == 200 and json.loads(body)["state"] == "APPLIED"
    assert '"Hello, "' in (repo / "services/demo/greeting.py").read_text()
    status, body, _ = request(base, f"/api/selfrepair/{rid}/undo", owner, {"confirm": True})
    assert status == 200 and json.loads(body)["state"] == "UNDONE"
    assert (repo / "services/demo/greeting.py").read_text() == BUGGY
    assert request(base, f"/api/selfrepair/{rid}/discard", owner, {})[0] == 409       # applied once: kept


def test_screenshot_is_kept_private_and_restart_needs_the_hook(server):
    base, owner, admin, svc, repo = server
    status, body, _ = request(base, "/api/selfrepair/report", owner, {
        "text": REPORT, "screenshot_b64": PNG, "screenshot_mime": "image/png", "confirm": True})
    assert status == 400 and b"Confirm" in body                    # consent for the screenshot itself
    status, body, _ = request(base, "/api/selfrepair/report", owner, {
        "text": REPORT, "screenshot_b64": PNG, "screenshot_mime": "image/png", "screenshot_consent": True,
        "confirm": True})
    rid = json.loads(body)["id"]
    status, data, headers = request(base, f"/api/selfrepair/{rid}/screenshot", owner)
    assert status == 200 and data == base64.b64decode(PNG) and headers["Content-Type"] == "image/png"
    assert "no-store" in headers["Cache-Control"]
    assert request(base, f"/api/selfrepair/{rid}/screenshot", admin)[0] == 403
    assert request(base, f"/api/selfrepair/{rid}/discard", owner, {})[0] == 200
    assert request(base, "/api/selfrepair/restart", owner, {"confirm": True})[0] == 409   # no hook here
    fired = []
    svc.restart_hook = lambda: fired.append(1)
    assert request(base, "/api/selfrepair/restart", owner, {})[0] == 400
    assert request(base, "/api/selfrepair/restart", owner, {"confirm": True})[0] == 200 and fired == [1]


def test_intelligence_live_data_comes_from_hoods_own_ledger(server, tmp_path):
    base, owner, admin, svc, repo = server
    assert request(base, "/api/intelligence/live")[0] == 401
    cc = CostController(BudgetSettings(), ledger_path=tmp_path / "ledger.sqlite3")
    usage = ModelUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, estimated_cost_usd=0.0025)
    cc.settle(cc.reserve("conv_abc", 0.01), usage, cost_measured=True, provider="gemini", model="gemini-3.8-flash")
    router = ModelRouter(SystemConfig(), price_table={"gemini": {"gemini-3.8-flash": ModelPrice(
        0.00075, 0.00375, "2026-10-09", "test")}})
    router.cost_controller = cc
    routes.SERVICES["router"] = router
    status, body, _ = request(base, "/api/intelligence/live", owner)
    assert status == 200, body
    live = json.loads(body)
    spend = live["spend"]
    assert spend["available"] and len(spend["recent"]) == 1
    assert spend["recent"][0]["kind"] == "chat" and spend["recent"][0]["cost_usd"] == pytest.approx(0.0025)
    assert sum(d["calls"] for d in spend["days"]) == 1
    assert spend["by_model"][0]["model"] == "gemini-3.8-flash" and spend["by_model"][0]["prompt_tokens"] == 100
    assert live["missions"] == {"available": False}                 # no agent engine attached: says so
    assert live["self_repair"]["available"] is True
    assert set(live["environment"]) >= {"sandbox_ready", "sandbox_problem"}


def test_intelligence_summary_shows_no_scores_until_measured(server):
    base, owner, admin, svc, repo = server
    status, body, _ = request(base, "/api/intelligence/summary", owner)
    data = json.loads(body)
    assert status == 200 and data["measured"] is False
    assert data["frontier_gap"] is None and data["level_1_score"] is None and data["level_3_score"] is None
    assert "Gemini 2.5" not in body.decode() and "92" not in json.dumps(data)
