"""VoiceCallService: the assembled voice-call system, one object the HTTP layer and Hood's runtime use."""
from __future__ import annotations

import hmac
from typing import Any, Dict, Optional

from .bridge import HoodVoiceBridge
from .config import VoiceCallConfig
from .confirm import ConfirmationService
from .contracts import Forbidden, HoodEvent, EventSeverity, NotConfigured, RateLimited
from .cost import VoiceCostTracker
from .devices import DeviceRegistry
from .dispatcher import CallDispatcher
from .events import VoiceEventDispatcher
from .policy import CallPermissionManager, OwnerCallPolicy, SUPPORTED_LANGUAGES, effective_settings
from .profiles import PROFILES, get_profile, minutes_cost_table
from .push import PushNotificationService
from .sessions import VoiceSessionManager
from .store import VoiceStore


class VoiceCallService:
    def __init__(self, *, cfg: VoiceCallConfig, store: VoiceStore, devices: DeviceRegistry, transport: Any,
                 cost: VoiceCostTracker, perms: CallPermissionManager, events: VoiceEventDispatcher,
                 confirm: ConfirmationService, bridge: HoodVoiceBridge, sessions: VoiceSessionManager,
                 push: PushNotificationService, policy: OwnerCallPolicy, dispatcher: CallDispatcher,
                 owner_resolver: Any = None, speech: Any = None, clock=None):
        self.cfg, self.store, self.devices, self.transport = cfg, store, devices, transport
        self.cost, self.perms, self.events, self.confirm = cost, perms, events, confirm
        self.bridge, self.sessions, self.push = bridge, sessions, push
        self.policy, self.dispatcher = policy, dispatcher
        self.owner_resolver = owner_resolver       # () -> owner user_id (single-owner Hood), used by event sources
        self.speech = speech                       # Hood's existing GeminiVoice for the cascade mode
        self._failures: Dict[str, list] = {}

    # ------------------------------------------------------------------ authentication for non-cookie clients
    def _throttle(self, key: str) -> None:
        import time
        now = time.time()
        q = [t for t in self._failures.get(key, []) if now - t < 300]
        if len(q) >= 10:
            raise RateLimited("Too many failed attempts; wait a few minutes")
        self._failures[key] = q

    def _fail(self, key: str) -> None:
        import time
        self._failures.setdefault(key, []).append(time.time())

    def authenticate_device(self, header: str, client_ip: str = "") -> Dict[str, Any]:
        """``Authorization: HoodDevice <device_id>.<secret>`` -> device row; raises Forbidden."""
        scheme, _, rest = (header or "").partition(" ")
        device_id, _, secret = rest.strip().partition(".")
        key = f"dev:{client_ip}:{device_id[:40]}"
        self._throttle(key)
        if scheme != "HoodDevice":
            raise Forbidden("Device credential required")
        dev = self.devices.authenticate(device_id, secret)
        if dev is None:
            self._fail(key)
            raise Forbidden("Invalid device credential")
        return dev

    def authenticate_pairing(self, header: str, client_ip: str = "") -> Dict[str, Any]:
        """Pairing is the one route with no credential: the one-time code in the body is the proof. Every attempt
        counts against a per-IP throttle (on top of the code check's own global failure limit)."""
        key = f"pair:{client_ip}"
        self._throttle(key)
        self._fail(key)
        return {"ip": client_ip}

    def authenticate_agent(self, header: str, client_ip: str = ""):
        scheme, _, token = (header or "").partition(" ")
        key = f"agent:{client_ip}"
        self._throttle(key)
        if scheme != "Bearer":
            raise Forbidden("Agent credential required")
        try:
            return self.sessions.verify_agent_token(token.strip())
        except Forbidden:
            self._fail(key)
            raise

    # ------------------------------------------------------------------ status and cost views
    def status(self, owner_id: str) -> Dict[str, Any]:
        prof = self.sessions.profile()
        return {"enabled": self.cfg.enabled, "transport": getattr(self.transport, "name", None),
                "transport_ready": self.transport is not None, "apns_ready": self.push.configured,
                "provider": {"id": prof.id, "mode": prof.mode, "model": prof.model, "voice": prof.voice,
                             "worst_case_usd_per_minute": round(prof.worst_case_usd_per_minute, 5),
                             "price_source": prof.source, "price_as_of": prof.as_of, "quoted": prof.quoted},
                "languages": SUPPORTED_LANGUAGES, "settings": effective_settings(self.store, self.cfg, owner_id),
                "spend": self.cost.summary(owner_id),
                "active_call": next((c.public() for c in self.store.active_calls(owner_id)), None),
                "devices": self.devices.list(owner_id)}

    def cost_scenarios(self) -> Dict[str, Any]:
        out = {}
        for pid, prof in PROFILES.items():
            out[pid] = minutes_cost_table(prof, [100, 300, 600, 1000], livekit_usd_per_min=self.cfg.livekit_usd_per_minute)
        return out

    # ------------------------------------------------------------------ event entry points used by Hood
    def emit_event(self, event: HoodEvent, owner_id: Optional[str] = None, *, force_call: bool = False) -> Dict[str, Any]:
        owner = owner_id or (self.owner_resolver() if self.owner_resolver else None)
        if not owner:
            return {"decision": "drop", "reason": "no owner"}
        return self.dispatcher.submit(owner, event, force_call=force_call)

    def shutdown(self) -> None:
        self.sessions.shutdown()
