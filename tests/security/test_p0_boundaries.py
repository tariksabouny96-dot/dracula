"""P0 execution-boundary regressions (Batch 1).

Each test checks an observable postcondition (state on disk, audit rows,
HTTP status), not just a function's return value.
"""
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from packages.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderName
from packages.config import SystemConfig
from packages.config.pricing import ModelPrice
from packages.security import StopLatch, EmergencyStopActive, confine_path, PathConfinementError
from services.audit.service import AuditService
from services.auth.auth_service import AuthenticationService, UserRole
from services.core.emergency_stop import EmergencyStopController
from services.model_gateway.base import BaseModelProvider, ProviderError
from services.model_gateway.cost_controller import CostController, BudgetExceededError
from services.model_gateway.router import ModelRouter
from services.policy.approval_service import ApprovalService
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.tool_gateway.tools import FSReadFileTool, FSWriteFileTool
from ui.server import JarvisServer, JarvisUIHandler
from tests.hardening.test_remediation import request, csrf_token


# ---------------------------------------------------------------- paths
@pytest.mark.parametrize("bad", ["../x", "a/../../x", "C:\\Windows\\System32", "\\\\host\\share\\f",
                                 "", "a\x00b", "/etc/passwd"])
def test_confine_path_rejects_escapes(tmp_path, bad):
    with pytest.raises(PathConfinementError):
        confine_path(tmp_path, bad)


def test_confine_path_rejects_symlink_escape_and_allows_inside(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "_outside")
    outside.mkdir()
    try:
        (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("BLOCKED_TARGET: symlink creation needs privileges on this host")
    with pytest.raises(PathConfinementError):
        confine_path(tmp_path, "link/secret.txt")
    assert confine_path(tmp_path, "sub/file.txt") == (tmp_path / "sub/file.txt").resolve()
    assert confine_path(tmp_path, str(tmp_path / "abs.txt")) == (tmp_path / "abs.txt").resolve()


# ---------------------------------------------------------------- emergency stop
def _gateway(tmp_path, latch=None):
    audit = AuditService(db_path=tmp_path / "audit.db")
    gw = ToolGateway(approval_service=ApprovalService(), audit_service=audit,
                     workspace_root=tmp_path, stop_latch=latch)
    gw.register_tool(FSReadFileTool(gw))
    gw.register_tool(FSWriteFileTool(gw))
    return gw, audit


def _audit_rows(audit, decision):
    with sqlite3.connect(audit.db_path) as db:
        return db.execute("SELECT action, error FROM audit_events WHERE policy_decision=?", (decision,)).fetchall()


def test_stop_blocks_every_tool_and_grant_and_is_audited(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    gw, audit = _gateway(tmp_path)
    assert gw.invoke_tool("fs_read_file", {"path": "a.txt"}) == "hello"
    stop = EmergencyStopController(gw, audit)
    report = stop.trigger_stop("test")
    assert report["latch_engaged"] is True and report["capabilities_revoked"] is True
    assert report["subsystems"]["browser"]["status"] == "NOT_ATTACHED"
    # Even an L0 read is refused once stopped, and the refusal is audited.
    with pytest.raises(PermissionDeniedError, match="Emergency stop"):
        gw.invoke_tool("fs_read_file", {"path": "a.txt"})
    with pytest.raises(EmergencyStopActive):
        gw.issue_capability_grant("fs:write", "workspace_root", "Hood")
    assert any("Emergency stop" in (err or "") for _, err in _audit_rows(audit, "DENY"))
    with pytest.raises(PermissionError):
        stop.reset_stop("someone", is_root_owner=False)
    stop.reset_stop("owner", is_root_owner=True)
    assert gw.invoke_tool("fs_read_file", {"path": "a.txt"}) == "hello"


def test_stop_report_never_claims_a_failed_halt(tmp_path):
    gw, audit = _gateway(tmp_path)

    class BrokenBrowser:
        def close(self):
            raise RuntimeError("browser wedged")

    report = EmergencyStopController(gw, audit, browser_service=BrokenBrowser()).trigger_stop("t")
    assert report["subsystems"]["browser"]["status"] == "ERROR"
    assert "browser" in report["incomplete"]


def test_stop_latch_survives_restart(tmp_path):
    state = tmp_path / "stop.json"
    gw, audit = _gateway(tmp_path, StopLatch(state))
    EmergencyStopController(gw, audit).trigger_stop("before crash")
    # New process: a fresh latch on the same state file is still engaged.
    gw2, audit2 = _gateway(tmp_path, StopLatch(state))
    ctl = EmergencyStopController(gw2, audit2)
    assert ctl.is_active
    with pytest.raises(PermissionDeniedError):
        gw2.invoke_tool("fs_read_file", {"path": "x"})
    state.write_text("{not json")
    assert StopLatch(state).engaged, "unreadable stop state must fail closed"


# ---------------------------------------------------------------- approvals
def _approved_write(gw, params, principal="Hood", ttl=60):
    import hashlib
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    req = gw.approval_service.create_request(task_id="t1", action_type="fs_write_file", target=params["path"],
                                             reason="test", options=[{"parameter_sha256": digest}],
                                             principal=principal, ttl_seconds=ttl)
    gw.approval_service.resolve_request(req.approval_id, True, resolved_by="owner")
    gw.issue_capability_grant("fs:write", "workspace_root", "Hood")
    return req


def test_expired_approval_is_refused(tmp_path):
    gw, _ = _gateway(tmp_path)
    params = {"path": "out.txt", "content": "x"}
    req = _approved_write(gw, params)
    req.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(PermissionDeniedError, match="expired"):
        gw.invoke_tool("fs_write_file", params, task_id="t1", approval_id=req.approval_id)
    assert not (tmp_path / "out.txt").exists()


def test_approval_bound_to_principal(tmp_path):
    gw, _ = _gateway(tmp_path)
    params = {"path": "out.txt", "content": "x"}
    req = _approved_write(gw, params, principal="engineer-agent")
    with pytest.raises(PermissionDeniedError, match="different principal"):
        gw.invoke_tool("fs_write_file", params, caller_agent="Hood", task_id="t1", approval_id=req.approval_id)
    assert not (tmp_path / "out.txt").exists()


def test_pending_approval_expires_and_cannot_be_resolved(tmp_path):
    svc = ApprovalService()
    req = svc.create_request("t", "fs_write_file", "f", "r", ttl_seconds=0)
    with pytest.raises(ValueError, match="EXPIRED"):
        svc.resolve_request(req.approval_id, True)
    assert svc.list_pending() == []


# ---------------------------------------------------------------- sessions
def test_session_tokens_are_not_stored_in_plaintext(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    sess = auth.authenticate("owner", "OwnerPassword123!")
    with sqlite3.connect(tmp_path / "auth.db") as db:
        stored = [r[0] for r in db.execute("SELECT session_token FROM sessions")]
    assert sess.session_token not in stored
    assert auth.validate_session(sess.session_token).user_id == sess.user_id
    # The listed id is the digest; it is not usable as a credential.
    listed = auth.list_active_sessions(sess.user_id)[0]["session_id"]
    assert auth.validate_session(listed) is None
    assert auth.revoke_session_id(sess.user_id, listed) is True
    assert auth.validate_session(sess.session_token) is None


def test_unknown_user_login_costs_a_password_hash(tmp_path, monkeypatch):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    calls = []
    original = AuthenticationService.verify_password
    monkeypatch.setattr(AuthenticationService, "verify_password",
                        staticmethod(lambda *a: calls.append(1) or original(*a)))
    assert auth.authenticate("ghost", "whatever") is None
    assert calls, "unknown usernames must not return faster than real ones"


def test_non_owner_cannot_revoke_foreign_session(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    viewer = auth.create_user(root["user_id"], "viewer", "Viewer", "ViewerPassword123!", UserRole.VIEWER)
    owner_sess = auth.authenticate("owner", "OwnerPassword123!")
    owner_id = auth.list_active_sessions(root["user_id"])[0]["session_id"]
    assert auth.revoke_session_id(viewer.user_id, owner_id) is False
    assert auth.validate_session(owner_sess.session_token) is not None


# ---------------------------------------------------------------- HTTP boundary
@pytest.fixture
def http(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "viewer", "Viewer", "ViewerPassword123!", UserRole.VIEWER)
    gw, audit = _gateway(tmp_path)
    stop = EmergencyStopController(gw, audit)
    srv = JarvisServer(port=0, auth_service=auth, emergency_stop=stop)
    srv.start()
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    try:
        yield base, auth, stop
    finally:
        srv.stop()


def _raw(base, path, headers=None, data=None):
    req = urllib.request.Request(base + path, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_dns_rebinding_host_is_refused(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")  # NOT initialized: setup is the prize
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    try:
        body = json.dumps({"username": "evil", "password": "EvilPassword123!"}).encode()
        status, _, _ = _raw(base, "/api/auth/init", {"Host": "attacker.example:80",
                                                     "Content-Type": "application/json"}, body)
        assert status == 421
        assert not auth.is_initialized()
        assert _raw(base, "/", {"Host": "attacker.example"})[0] == 421
    finally:
        srv.stop()


def test_csrf_required_for_cookie_sessions_not_for_bearer(http):
    base, auth, _ = http
    sess = auth.authenticate("owner", "OwnerPassword123!")
    cookie = "hood_session=" + sess.session_token
    status, _, _ = request(base, "/api/auth/sessions/revoke_others", cookie, {}, csrf=False)
    assert status == 403
    status, _, _ = request(base, "/api/auth/sessions/revoke_others", cookie, {"x": 1})
    assert status == 200
    status, _, _ = _raw(base, "/api/auth/sessions/revoke_others",
                        {"Authorization": "Bearer " + sess.session_token, "Content-Type": "application/json"}, b"{}")
    assert status == 200


def test_security_headers_on_static_and_api(http):
    base, _, _ = http
    status, _, headers = _raw(base, "/")
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_emergency_stop_reset_is_root_only_and_explicit(http):
    base, auth, stop = http
    status, body, _ = _raw(base, "/api/emergency_stop", {"Content-Type": "application/json"}, b"{}")
    assert status == 200 and json.loads(body)["latch_engaged"] is True
    viewer = "hood_session=" + auth.authenticate("viewer", "ViewerPassword123!").session_token
    assert request(base, "/api/emergency_stop/reset", viewer, {"confirm": True})[0] == 403
    owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
    assert request(base, "/api/emergency_stop/reset", owner, {})[0] == 400
    assert stop.is_active
    assert request(base, "/api/emergency_stop/reset", owner, {"confirm": True})[0] == 200
    assert not stop.is_active and not stop.tool_gateway.stop_latch.engaged


# ---------------------------------------------------------------- budgets
class PaidStub(BaseModelProvider):
    """Deterministic paid-provider stub (contract test only; proves nothing about live APIs)."""

    def __init__(self, usage=None, error=None):
        super().__init__(ProviderName.GEMINI, enabled=True)
        self.usage, self.error, self.calls = usage, error, 0

    def is_healthy(self):
        return True

    def resolve_model(self, request):
        return "priced-model"

    def invoke(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return ModelResponse(text="ok", provider=ProviderName.GEMINI, model_name="priced-model",
                             usage=self.usage or ModelUsage(), latency_ms=1)


def _router(stub, prices, **budget):
    cfg = SystemConfig()
    for k, v in budget.items():
        setattr(cfg.budgets, k, v)
    router = ModelRouter(cfg, CostController(cfg.budgets), price_table=prices)
    for name in list(router.providers):
        router.providers[name].enabled = False
    router.register_provider(ProviderName.GEMINI, stub)
    return router


PRICES = {"gemini": {"priced-model": ModelPrice(1.0, 1.0, "2026-10-01", "test fixture")}}


def test_unknown_price_refuses_paid_call():
    stub = PaidStub(usage=ModelUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    router = _router(stub, {})
    with pytest.raises(ProviderError, match="unknown cost"):
        router.invoke(ModelRequest(prompt="hi", max_tokens=10, task_id="t"))
    assert stub.calls == 0


def test_missing_usage_is_charged_not_zero():
    stub = PaidStub(usage=ModelUsage())  # provider omitted usage
    router = _router(stub, PRICES)
    router.invoke(ModelRequest(prompt="hi", max_tokens=100, task_id="t"))
    record = router.cost_controller.call_history[-1]
    assert record["cost_measured"] is False and record["cost_usd"] > 0
    assert router.cost_controller.get_task_spend("t") == pytest.approx(record["reserved_usd"])


def test_provider_error_after_send_is_charged():
    router = _router(PaidStub(error=ProviderError("timeout")), PRICES)
    with pytest.raises(ProviderError):
        router.invoke(ModelRequest(prompt="hi", max_tokens=100, task_id="t"))
    assert router.cost_controller.get_task_spend("t") > 0


def test_parallel_reservations_cannot_overspend():
    cc = CostController()
    cc.budgets.max_task_spend_usd = 1.0
    cc.budgets.max_task_model_calls = 1000
    granted, refused = [], []

    def worker():
        try:
            granted.append(cc.reserve("t", 0.3))
        except BudgetExceededError:
            refused.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(granted) == 3 and len(refused) == 17


def test_spend_cap_blocks_new_paid_calls():
    stub = PaidStub(usage=ModelUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000))
    router = _router(stub, PRICES, max_task_spend_usd=0.5)
    with pytest.raises(BudgetExceededError):
        router.invoke(ModelRequest(prompt="hi", max_tokens=1000, task_id="t"))
    assert stub.calls == 0


# ---------------------------------------------------------------- vault
@pytest.mark.skipif(os.name != "posix", reason="BLOCKED_TARGET: POSIX permission bits; Windows ACL check not implemented")
def test_vault_refuses_world_readable_key_and_rotates(tmp_path, monkeypatch):
    from packages.auth.vault import SecretVault
    monkeypatch.delenv("HOOD_VAULT_KEY", raising=False)
    vault = SecretVault(vault_path=tmp_path / "vault.enc")
    vault.set_secret("gemini", "api_key", "s3cret")
    old_cipher = (tmp_path / "vault.enc").read_bytes()
    vault.rotate_key()
    assert (tmp_path / "vault.enc").read_bytes() != old_cipher
    assert SecretVault(vault_path=tmp_path / "vault.enc").get_secret("SECRET://gemini/api_key") == "s3cret"
    os.chmod(tmp_path / ".vault_key", 0o644)
    with pytest.raises(PermissionError):
        SecretVault(vault_path=tmp_path / "vault.enc")


def test_vault_key_from_environment_is_not_written_to_disk(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from packages.auth.vault import SecretVault
    monkeypatch.setenv("HOOD_VAULT_KEY", Fernet.generate_key().decode())
    SecretVault(vault_path=tmp_path / "vault.enc").set_secret("openai", "api_key", "k")
    assert not (tmp_path / ".vault_key").exists()


def test_code_modifier_uses_canonical_confinement(tmp_path):
    from services.dev_executor.code_modifier import CodeModifier
    mod = CodeModifier(tmp_path)
    for bad in ("C:\\Windows\\win.ini", "../x.py", "a\x00b"):
        with pytest.raises(PermissionError):
            mod._validate_path(bad)
    assert mod._validate_path("pkg/mod.py") == (tmp_path / "pkg/mod.py").resolve()


def test_repo_pricing_file_loads_and_declares_source():
    from pathlib import Path
    from packages.config.pricing import load_price_table
    table = load_price_table(str(Path(__file__).resolve().parents[2] / "config" / "model_pricing.free-tier.json"))
    price = table["gemini"]["gemini-3.8-flash"]
    assert price.source.startswith("owner-declared") and price.as_of
