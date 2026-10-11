"""Builds the voice-call service and connects it to Hood's runtime and event sources.

Everything is inert unless ``HOOD_VOICECALL_ENABLED=1``: no network, no threads, no listeners.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from .bridge import HoodVoiceBridge
from .config import VoiceCallConfig
from .confirm import ConfirmationService
from .contracts import EventSeverity, HoodEvent
from .cost import VoiceCostTracker
from .devices import DeviceRegistry
from .dispatcher import CallDispatcher
from .events import VoiceEventDispatcher
from .policy import CallPermissionManager, OwnerCallPolicy
from .profiles import get_profile
from .push import PushNotificationService
from .service import VoiceCallService
from .sessions import VoiceSessionManager
from .store import VoiceStore

log = logging.getLogger("hood.voicecall")
ROOT_OWNER_ID = "user_root_owner_01"    # the id AuthenticationService gives the Root Owner


def build_voicecall(runtime: Any, data_dir: Optional[Path] = None, *, cfg: Optional[VoiceCallConfig] = None,
                    transport: Any = None, push: Optional[PushNotificationService] = None,
                    start_watchdog: bool = True) -> VoiceCallService:
    data_dir = Path(data_dir or runtime.data_dir)
    cfg = cfg or VoiceCallConfig.from_env(data_dir)
    firewall = getattr(runtime, "firewall", None)
    store = VoiceStore(data_dir)
    devices = DeviceRegistry(store)

    if transport is None and cfg.enabled and cfg.transport_ready:
        from .livekit import LiveKitTransport
        transport = LiveKitTransport(cfg, firewall=firewall)
        _seed_firewall(firewall, cfg)
    holder = {"t": transport}
    events = VoiceEventDispatcher(store, lambda: holder["t"])

    cc = getattr(getattr(runtime, "model_router", None), "cost_controller", None)
    alerts = []
    cost = VoiceCostTracker(store, cfg, cc, alert_sink=lambda kind, d: alerts.append((kind, d)))
    auth = getattr(runtime, "auth_service", None)
    perms = CallPermissionManager(store, devices, auth)
    interaction = getattr(runtime, "interaction_service", None)
    engine = getattr(runtime, "agent_engine", None)
    approvals = getattr(runtime, "approval_service", None)
    confirm = ConfirmationService(store, devices, cfg, interaction=interaction, approvals=approvals, engine=engine, auth=auth)
    bridge = HoodVoiceBridge(interaction, engine, store, cost, cfg, events, confirm)
    sessions = VoiceSessionManager(store, transport, cost, perms, devices, events, cfg,
                                   stop_latch=getattr(runtime, "stop_latch", None))
    push = push or PushNotificationService(cfg, firewall=firewall)
    policy = OwnerCallPolicy(store, cfg, devices, cost, sessions.profile)
    dispatcher = CallDispatcher(store, cfg, policy, sessions, devices, push, interaction=interaction)

    def owner_id() -> Optional[str]:
        """Hood has a single Root Owner; events about Hood itself go to that account."""
        if auth is None:
            return None
        try:
            u = auth.get_user_by_id(ROOT_OWNER_ID)
            return u["user_id"] if u and str(u.get("role")).endswith("ROOT_OWNER") and u.get("is_active", True) else None
        except Exception:
            return None

    svc = VoiceCallService(cfg=cfg, store=store, devices=devices, transport=transport, cost=cost, perms=perms,
                           events=events, confirm=confirm, bridge=bridge, sessions=sessions, push=push,
                           policy=policy, dispatcher=dispatcher, owner_resolver=owner_id)
    cost.alert_sink = lambda kind, d: svc.emit_event(HoodEvent(
        type="info", severity=EventSeverity.MEDIUM, subject=f"voice_spend:{d['period']}:{d['threshold']}",
        summary=f"Voice spending reached {int(d['threshold'] * 100)}% of the {d['period']} limit", source="voicecall"))
    if cfg.enabled:
        sessions.recover()
        if start_watchdog and transport is not None:
            sessions.start()
        attach_event_sources(runtime, svc)
    return svc


def _seed_firewall(firewall: Any, cfg: VoiceCallConfig) -> None:
    if firewall is None:
        return
    import urllib.parse
    hosts = []
    h = urllib.parse.urlsplit(cfg.livekit_http_url).hostname
    if h:
        hosts.append(h)
    if cfg.apns_ready:
        hosts += ["api.push.apple.com", "api.sandbox.push.apple.com"]
    try:
        firewall.seed_defaults(hosts)
    except Exception:
        pass


# --------------------------------------------------------------------------- event sources
def attach_event_sources(runtime: Any, svc: VoiceCallService) -> None:
    """Connect real Hood signals to the dispatcher. Each handler is best effort and never raises."""
    approvals = getattr(runtime, "approval_service", None)
    if approvals is not None and hasattr(approvals, "listeners"):
        approvals.listeners.append(lambda req: _safe(svc, on_approval_created, req))
    attach_sentinel(svc, getattr(runtime, "sentinel_service", None))
    engine = getattr(runtime, "agent_engine", None)
    if engine is not None:
        prior = engine.on_outcome

        def outcome(owner, mission_id, objective, result, detail):
            if prior:
                prior(owner, mission_id, objective, result, detail)
            _safe(svc, on_mission_outcome, owner, mission_id, objective, result, detail)
        engine.on_outcome = outcome
        if hasattr(engine, "on_attention"):
            prior_attention = engine.on_attention

            def attention(owner, mission_id, objective, kind, detail):
                if prior_attention:
                    prior_attention(owner, mission_id, objective, kind, detail)
                _safe(svc, on_mission_attention, owner, mission_id, objective, kind, detail)
            engine.on_attention = attention


def attach_sentinel(svc: VoiceCallService, sentinel: Any) -> bool:
    """Route the Security Sentinel's HIGH/CRITICAL findings to the call dispatcher. The Sentinel is created by Hood's
    UI server, not by the runtime, so ``hood_cli ui`` calls this once the server exists. Safe to call twice."""
    if sentinel is None or not hasattr(sentinel, "listeners"):
        return False
    if not getattr(sentinel, "_voicecall_attached", False):
        sentinel.listeners.append(lambda finding: _safe(svc, on_finding, finding))
        sentinel._voicecall_attached = True
    return True


def _safe(svc: VoiceCallService, fn, *args) -> None:
    try:
        fn(svc, *args)
    except Exception as exc:
        log.warning("voicecall event handler failed (%s)", type(exc).__name__)


def on_approval_created(svc: VoiceCallService, req: Any) -> None:
    risk = getattr(req.risk_level, "value", str(req.risk_level))
    sev = EventSeverity.HIGH if risk in ("L3", "L4", "L5") else EventSeverity.MEDIUM
    svc.emit_event(HoodEvent(type="approval_required", severity=sev, subject=f"approval:{req.approval_id}",
                             summary=f"I need your approval: {req.action_type}",
                             detail=f"{req.reason}. Target: {req.target}.", source="approvals",
                             data={"approval_id": req.approval_id}))


def on_finding(svc: VoiceCallService, finding: Any) -> None:
    sev_name = getattr(finding.severity, "value", str(finding.severity))
    if sev_name not in ("CRITICAL", "HIGH"):
        return
    title = getattr(finding, "title", None) or getattr(finding, "description", "security finding")
    svc.emit_event(HoodEvent(type="security_critical",
                             severity=EventSeverity.CRITICAL if sev_name == "CRITICAL" else EventSeverity.MEDIUM,
                             subject=f"finding:{finding.finding_id}", summary=f"Security finding: {str(title)[:120]}",
                             detail=str(getattr(finding, "description", ""))[:600], source="sentinel"))


def on_mission_attention(svc: VoiceCallService, owner: str, mission_id: str, objective: str, kind: str, detail: str) -> None:
    """A mission needs the owner: a plan to approve, or a local run to approve. Rings only if the owner's call rules allow
    (event type 'approval_required'); the decision itself is made with Face ID or in the console, never by voice."""
    obj = (objective or "")[:90]
    what = "plan" if kind == "plan_ready" else "local run"
    svc.emit_event(HoodEvent(type="approval_required", severity=EventSeverity.HIGH, subject=f"mission-{kind}:{mission_id}",
                             summary=f"A mission {what} is waiting for your approval: {obj}", detail=(detail or "")[:400],
                             source="missions", data={"mission_id": mission_id}), owner_id=owner)


def on_mission_outcome(svc: VoiceCallService, owner: str, mission_id: str, objective: str, result: str, detail: str) -> None:
    obj = (objective or "")[:90]
    if result == "success":
        svc.emit_event(HoodEvent(type="info", severity=EventSeverity.INFO, subject=f"mission:{mission_id}",
                                 summary=f"Mission finished: {obj}", source="missions"), owner_id=owner)
    elif "block" in result.lower() or "owner" in result.lower():
        svc.emit_event(HoodEvent(type="decision_blocking", severity=EventSeverity.HIGH, subject=f"mission:{mission_id}",
                                 summary=f"A mission is waiting for your decision: {obj}", detail=detail[:400],
                                 source="missions"), owner_id=owner)
    else:
        svc.emit_event(HoodEvent(type="operational_failure", severity=EventSeverity.MEDIUM, subject=f"mission:{mission_id}",
                                 summary=f"Mission {result}: {obj}", detail=detail[:400], source="missions"), owner_id=owner)
