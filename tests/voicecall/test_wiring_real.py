"""Hood-initiated calling driven by REAL Hood services: ApprovalService, the mission engine's attention/outcome hooks and the
Security Sentinel, assembled by build_voicecall exactly as `hood_cli ui` does. SIMULATED: APNs (FakePush), LiveKit (in-memory),
the model that plans a mission (script). Before this test the event sources were only exercised by hand-built events."""
from types import SimpleNamespace

import pytest

from packages.contracts import RiskLevel
from services.agents import AgentEngine
from services.model_gateway.cost_controller import CostController
from services.policy.approval_service import ApprovalService
from services.sentinel.contracts import FindingSeverity, SentinelFinding, VulnerabilityCategory
from services.sentinel.sentinel_service import SecuritySentinelService
from services.voicecall.config import Secret, VoiceCallConfig
from services.voicecall.fake import InMemoryTransport
from services.voicecall.wiring import attach_sentinel, build_voicecall
from tests.agents.scripted_provider import ScriptedModel
from tests.voicecall.conftest import FakePush, OWNER, make_keypair

OBJECTIVE = "Build a small responsive notes web app with a JSON API and a mobile friendly index page."


@pytest.fixture
def stack(tmp_path):
    cfg = VoiceCallConfig(enabled=True, livekit_url="wss://fake.livekit.test", livekit_api_key="devkey",
                          livekit_api_secret=Secret("s" * 40), agent_secret=Secret("a" * 48), quiet_start="00:00", quiet_end="00:00",
                          max_calls_per_hour=10)
    engine = AgentEngine(tmp_path / "engine", invoke=ScriptedModel(), allow_simulated=True)
    auth = SimpleNamespace(get_user_by_id=lambda uid: {"user_id": uid, "username": "owner", "role": "ROOT_OWNER", "is_active": True})
    runtime = SimpleNamespace(data_dir=tmp_path, firewall=None, model_router=SimpleNamespace(cost_controller=CostController()),
                              approval_service=ApprovalService(), agent_engine=engine, auth_service=auth, stop_latch=None,
                              interaction_service=None)
    push = FakePush()
    svc = build_voicecall(runtime, tmp_path, cfg=cfg, transport=InMemoryTransport(), push=push, start_watchdog=False)
    reg = svc.devices.register(OWNER, "Owner iPhone", make_keypair()[1])
    svc.devices.set_push_tokens(reg["device_id"], voip_token="ab" * 32, alert_token="cd" * 32, apns_env="sandbox")
    return SimpleNamespace(runtime=runtime, svc=svc, push=push, engine=engine, device_id=reg["device_id"])


def test_a_real_approval_request_rings_the_owners_phone(stack):
    stack.runtime.approval_service.create_request(task_id="t", action_type="task_execution", target="repo", reason="Run the migration",
                                                  risk_level=RiskLevel.L3, recommended_option="approve")
    assert len(stack.push.voip) == 1 and stack.push.voip[0]["device"] == stack.device_id
    assert "approval" in stack.push.voip[0]["reason"].lower()


def test_a_real_mission_plan_waiting_for_approval_rings_and_never_starts_anything(stack):
    created = stack.engine.create_mission(OWNER, OBJECTIVE, budget_usd=1.0)         # the engine's real attention hook fires
    assert created["state"] == "AWAITING_PLAN_APPROVAL"
    assert len(stack.push.voip) == 1
    assert "plan is waiting for your approval" in stack.push.voip[0]["reason"]
    assert stack.engine.status(OWNER, created["mission_id"])["state"] == "AWAITING_PLAN_APPROVAL"   # a call approves nothing


def test_a_critical_sentinel_finding_rings_once_it_is_attached_to_the_server_created_sentinel(stack, tmp_path):
    sentinel = SecuritySentinelService(registry_path=tmp_path / "findings.json")
    finding = lambda sev, n: SentinelFinding(category=VulnerabilityCategory.ACCESS_CONTROL, severity=sev, title=f"Exposed admin port {n}",
                                             affected_component="ui", description="d", evidence="e")
    sentinel.register_finding(finding(FindingSeverity.CRITICAL, 0))          # before wiring: nothing happens
    assert stack.push.voip == []
    assert attach_sentinel(stack.svc, sentinel) is True and attach_sentinel(stack.svc, sentinel) is True   # idempotent
    sentinel.register_finding(finding(FindingSeverity.LOW, 1))                # not serious enough
    assert stack.push.voip == []
    sentinel.register_finding(finding(FindingSeverity.CRITICAL, 2))
    assert len(stack.push.voip) == 1 and "Security finding" in stack.push.voip[0]["reason"]
    assert len(sentinel.listeners) == 1                                       # attached once, not twice


def test_mission_outcomes_notify_but_do_not_ring(stack):
    stack.engine._emit_outcome(OWNER, "agm_" + "1" * 32, OBJECTIVE, "success", "verified PASS; artifact delivered")
    stack.engine._emit_outcome(OWNER, "agm_" + "2" * 32, OBJECTIVE, "failure", "tests failed")
    assert stack.push.voip == []                                              # ordinary completions/failures never ring
    assert len(stack.push.alerts) >= 1                                        # ... they are notifications


def test_nothing_is_attached_while_voice_calling_is_off(tmp_path):
    cfg = VoiceCallConfig(enabled=False)
    approvals = ApprovalService()
    runtime = SimpleNamespace(data_dir=tmp_path, firewall=None, model_router=None, approval_service=approvals, agent_engine=None,
                              auth_service=None, interaction_service=None)
    build_voicecall(runtime, tmp_path, cfg=cfg, start_watchdog=False)
    assert approvals.listeners == []
