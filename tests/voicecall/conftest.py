"""Shared fixtures for voice-call tests.

SIMULATED parts (clearly named): ``InMemoryTransport`` (no media), ``FakePush`` (no APNs), ``FakeEngine``
(no model planning) and the scripted model router. REAL parts: store, devices, cost controller, approval
service, interaction service, policy, session state machine, confirmation crypto.
"""
import base64
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from packages.config.settings import BudgetSettings
from packages.contracts import ModelResponse, ModelUsage, ProviderName
from services.interaction.interaction_service import InteractionService
from services.model_gateway.cost_controller import CostController
from services.policy.approval_service import ApprovalService
from services.voicecall.bridge import HoodVoiceBridge
from services.voicecall.config import Secret, VoiceCallConfig
from services.voicecall.confirm import ConfirmationService
from services.voicecall.cost import VoiceCostTracker
from services.voicecall.devices import DeviceRegistry
from services.voicecall.dispatcher import CallDispatcher
from services.voicecall.events import VoiceEventDispatcher
from services.voicecall.fake import InMemoryTransport
from services.voicecall.policy import CallPermissionManager, OwnerCallPolicy
from services.voicecall.push import PushResult
from services.voicecall.sessions import VoiceSessionManager
from services.voicecall.store import VoiceStore

OWNER = "user_root_owner_01"


class FakeClock:
    def __init__(self, t=1_790_000_000.0):          # 2026-09-21T... (daytime UTC)
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class FakePush:
    """SIMULATED APNs: records what would be sent."""
    configured = True

    def __init__(self):
        self.voip, self.alerts = [], []
        self.voip_result = PushResult(True, 200, "", "apns-id-1")
        self.alert_result = PushResult(True, 200, "", "apns-id-2")

    def send_voip_call(self, device, **kw):
        self.voip.append({"device": device["device_id"], **kw})
        return self.voip_result

    def send_alert(self, device, **kw):
        self.alerts.append({"device": device["device_id"], **kw})
        return self.alert_result


class FakeEngine:
    """SIMULATED agent engine: records plan approvals, plans nothing."""

    def __init__(self):
        self.missions = {}
        self.created = []
        self.approved, self.cancelled = [], []

    def add(self, mid, owner, state="AWAITING_PLAN_APPROVAL", objective="Build a coffee shop website", budget=1.0):
        self.missions[mid] = {"id": mid, "owner": owner, "state": state, "objective": objective,
                              "plan_sha256": "ab" * 32, "budget_usd": budget}

    def create_mission(self, owner, objective, budget_usd=1.0, profile="python_app"):
        mid = "agm_" + "%032x" % (len(self.created) + 1)
        self.created.append((owner, objective, budget_usd, profile))
        self.add(mid, owner, objective=objective, budget=budget_usd)
        return {"id": mid, "state": "AWAITING_PLAN_APPROVAL"}

    def list(self, owner):
        return [dict(m) for m in self.missions.values() if m["owner"] == owner]

    def status(self, owner, mid):
        m = self.missions.get(mid)
        if not m or m["owner"] != owner:
            raise KeyError(mid)
        return dict(m)

    def approve_plan(self, owner, mid, plan_sha256, approver):
        assert plan_sha256 == self.missions[mid]["plan_sha256"]
        self.approved.append((owner, mid, approver))
        self.missions[mid]["state"] = "QUEUED"
        return {"state": "QUEUED"}

    def cancel(self, owner, mid, actor):
        self.cancelled.append((owner, mid, actor))
        self.missions[mid]["state"] = "CANCELLED"
        return {"state": "CANCELLED"}


class ScriptedRouter:
    """SIMULATED model: replies with fixed text; counts requests and charges a tiny cost."""

    def __init__(self, cost_controller=None, text="All systems answered.", cost=0.002):
        self.text, self.cc, self.cost = text, cost_controller, cost
        self.requests = []
        self.providers = {}

    def invoke(self, req):
        self.requests.append(req)
        if self.cc is not None:
            rid = self.cc.reserve(req.task_id, self.cost)
            self.cc.settle(rid, ModelUsage(estimated_cost_usd=self.cost), cost_measured=True, provider="stub", model="stub")
        return ModelResponse(text=self.text, provider=ProviderName.GEMINI, model_name="stub", usage=ModelUsage(),
                             latency_ms=1, is_mock=False)


def make_keypair():
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, base64.b64encode(spki).decode()


@pytest.fixture
def kit(tmp_path):
    clock = FakeClock()
    cfg = VoiceCallConfig(enabled=True, livekit_url="wss://fake.livekit.test", livekit_api_key="devkey",
                          livekit_api_secret=Secret("s" * 40), agent_secret=Secret("a" * 48),
                          apns_team_id="TEAM123456", apns_key_id="KEY1234567", apns_bundle_id="com.example.hood",
                          apns_key_path="/dev/null")
    store = VoiceStore(tmp_path)
    devices = DeviceRegistry(store)
    transport = InMemoryTransport()
    cc = CostController(BudgetSettings(max_daily_spend_usd=50.0, max_monthly_spend_usd=500.0, max_task_spend_usd=10.0))
    cost = VoiceCostTracker(store, cfg, cc, clock=clock)
    auth = SimpleNamespace(get_user_by_id=lambda uid: {"user_id": uid, "username": "owner", "role": "ROOT_OWNER", "is_active": True}
                           if uid == OWNER else {"user_id": uid, "username": "viewer", "role": "VIEWER", "is_active": True})
    perms = CallPermissionManager(store, devices, auth, clock=clock)
    events = VoiceEventDispatcher(store, lambda: transport)
    approvals = ApprovalService()
    router = ScriptedRouter(cc)
    interaction = InteractionService(commander=SimpleNamespace(model_router=router), approval_service=approvals, auth_service=None)
    engine = FakeEngine()
    confirm = ConfirmationService(store, devices, cfg, interaction=interaction, approvals=approvals, engine=engine,
                                  auth=auth, clock=clock)
    bridge = HoodVoiceBridge(interaction, engine, store, cost, cfg, events, confirm, clock=clock)
    sessions = VoiceSessionManager(store, transport, cost, perms, devices, events, cfg, clock=clock)
    push = FakePush()
    policy = OwnerCallPolicy(store, cfg, devices, cost, sessions.profile, clock=clock)
    dispatcher = CallDispatcher(store, cfg, policy, sessions, devices, push, interaction=interaction, clock=clock)
    key, pub = make_keypair()
    reg = devices.register(OWNER, "Owner iPhone", pub)
    devices.set_push_tokens(reg["device_id"], voip_token="ab" * 32, alert_token="cd" * 32, apns_env="sandbox")
    return SimpleNamespace(clock=clock, cfg=cfg, store=store, devices=devices, transport=transport, cc=cc, cost=cost,
                           perms=perms, events=events, approvals=approvals, router=router, interaction=interaction,
                           engine=engine, confirm=confirm, bridge=bridge, sessions=sessions, push=push, policy=policy,
                           dispatcher=dispatcher, key=key, pub=pub, device_id=reg["device_id"],
                           device_secret=reg["device_secret"], device=devices.get(reg["device_id"]), auth=auth)
