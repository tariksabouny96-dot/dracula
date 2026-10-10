"""Self-development over HTTP: owner-only apply, approval-bound, guardrail-safe.
Uses a temp-workspace controller injected into SERVICES so it never touches the
real source tree."""
import json
import pytest

from services.auth.auth_service import AuthenticationService, UserRole
from services.evolution.self_development import SelfDevelopmentController
from services.policy.approval_service import ApprovalService
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    routes.load_modules()
    ws = tmp_path / "ws"
    (ws / "services" / "demo").mkdir(parents=True)
    (ws / "services" / "demo" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    appr = ApprovalService()
    controller = SelfDevelopmentController(workspace_root=ws, approval_service=appr,
                                           data_dir=tmp_path / "sd")
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "coder", "Coder", "CoderPassword123!", UserRole.OPERATOR)
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    # Inject AFTER start(): start() populates SERVICES (and pops selfdev when no
    # runtime is attached), so our temp-workspace controller must go in after.
    routes.SERVICES["selfdev"] = controller
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
    coder = "hood_session=" + auth.authenticate("coder", "CoderPassword123!").session_token
    try:
        yield base, owner, coder, controller, ws
    finally:
        srv.stop()
        routes.SERVICES.pop("selfdev", None)


def test_guardrail_proposal_refused_over_http(server):
    base, owner, coder, controller, ws = server
    r = request(base, "/api/selfdev/propose", owner,
                {"target_path": "services/firewall/policy.py", "content": "x=1\n",
                 "rationale": "nope", "confirm": True})
    assert r[0] in (400, 403)  # constitutional violation surfaces as a refusal


def test_propose_then_owner_applies(server):
    base, owner, coder, controller, ws = server
    # Propose (records only; live file unchanged).
    status, body, _ = request(base, "/api/selfdev/propose", owner,
                              {"target_path": "services/demo/mod.py", "content": "VALUE = 2\n",
                               "rationale": "bump", "confirm": True})
    assert status == 201, body
    pid = json.loads(body)["proposal_id"]
    assert (ws / "services" / "demo" / "mod.py").read_text() == "VALUE = 1\n"

    # Applying before approval is refused.
    assert request(base, f"/api/selfdev/proposals/{pid}/apply", owner, {"confirm": True})[0] == 403

    # Owner approves the bound request, then applies.
    appr_id = json.loads(body)["approval_id"]
    controller.approval_service.resolve_request(appr_id, approved=True, resolved_by="owner")
    status, body, _ = request(base, f"/api/selfdev/proposals/{pid}/apply", owner, {"confirm": True})
    assert status == 200 and json.loads(body)["state"] == "APPLIED"
    assert (ws / "services" / "demo" / "mod.py").read_text() == "VALUE = 2\n"


def test_non_owner_cannot_apply(server):
    base, owner, coder, controller, ws = server
    status, body, _ = request(base, "/api/selfdev/propose", owner,
                              {"target_path": "services/demo/mod.py", "content": "VALUE = 3\n",
                               "rationale": "bump", "confirm": True})
    pid = json.loads(body)["proposal_id"]
    controller.approval_service.resolve_request(json.loads(body)["approval_id"], approved=True, resolved_by="owner")
    # A non-owner (operator) lacks OWNERSHIP_ADMIN -> 403, file unchanged.
    assert request(base, f"/api/selfdev/proposals/{pid}/apply", coder, {"confirm": True})[0] == 403
    assert (ws / "services" / "demo" / "mod.py").read_text() == "VALUE = 1\n"


def test_owner_sees_the_exact_change_before_approving(server):
    """Phase 4 panel: a proposal shows its diff against the file as it is now."""
    base, owner, coder, controller, ws = server
    status, body, _ = request(base, "/api/selfdev/propose", owner,
                              {"target_path": "services/demo/mod.py", "content": "VALUE = 5\n",
                               "rationale": "bump", "confirm": True})
    pid = json.loads(body)["proposal_id"]
    status, body, _ = request(base, f"/api/selfdev/proposals/{pid}", owner)
    diff = json.loads(body)["diff"]
    assert status == 200 and "-VALUE = 1" in diff and "+VALUE = 5" in diff
    assert (ws / "services" / "demo" / "mod.py").read_text() == "VALUE = 1\n"
