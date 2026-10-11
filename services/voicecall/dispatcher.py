"""CallDispatcher: turns Hood events into (at most) one call or notification to the owner.

Flow for ``submit(event)``: record -> OwnerCallPolicy decides (dedupe, type, severity, quiet hours,
frequency, active call, devices, budget) -> ring via VoIP push, or send an ordinary notification, or drop.
A call that is not answered becomes a *missed call* with a push-notification fallback and a "call back"
path. Every decision is stored and correlated with the call that resulted from it.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Callable, Dict, List, Optional

from .bridge import speakable
from .config import VoiceCallConfig
from .contracts import CallRecord, EndReason, EventSeverity, HoodEvent, VoiceError
from .devices import DeviceRegistry
from .policy import OwnerCallPolicy, effective_settings
from .push import PushNotificationService
from .sessions import VoiceSessionManager, _grant_dict
from .store import VoiceStore


def _redact(text: str) -> str:
    try:
        from packages.logging.redactor import redact_string
        return redact_string(text)
    except Exception:
        return text


def compose_briefing(event: HoodEvent) -> str:
    base = event.detail.strip() or event.summary.strip()
    return speakable(_redact(f"This is Hood. {event.summary.strip()}. {base if base != event.summary.strip() else ''}"
                             " Shall I go through it with you?"), 600)


class CallDispatcher:
    def __init__(self, store: VoiceStore, cfg: VoiceCallConfig, policy: OwnerCallPolicy, sessions: VoiceSessionManager,
                 devices: DeviceRegistry, push: PushNotificationService, *, interaction: Any = None,
                 clock: Callable[[], float] = time.time):
        self.store, self.cfg, self.policy, self.sessions = store, cfg, policy, sessions
        self.devices, self.push, self.interaction, self.clock = devices, push, interaction, clock
        sessions.on_missed = self.on_missed

    # ------------------------------------------------------------------ entry point
    def submit(self, owner_id: str, event: HoodEvent, *, force_call: bool = False) -> Dict[str, Any]:
        event.event_id = event.event_id or "evt_" + secrets.token_hex(8)
        event.summary = _redact(event.summary)[:200]
        event.detail = _redact(event.detail)[:1200]
        decision = self.policy.evaluate(owner_id, event, force_call=force_call)
        self.store.record_event_decision(event.event_id, event.type, event.severity.value, event.subject, event.summary,
                                         event.source, decision.action)
        out: Dict[str, Any] = {"event_id": event.event_id, "decision": decision.action, "reason": decision.reason}
        if decision.action == "drop":
            return out
        if decision.action == "call":
            ringed = self._ring(owner_id, event)
            out.update(ringed)
            if ringed.get("call_id"):
                self.store.record_event_decision(event.event_id, event.type, event.severity.value, event.subject,
                                                 event.summary, event.source, "call", ringed["call_id"])
                return out
            out["decision"] = "notify"
            out["reason"] = f"call not placed ({ringed.get('error', 'unknown')}); notified instead"
        if decision.action == "notify_in_call":
            for c in self.store.active_calls(owner_id):
                self.sessions.events.emit(c.call_id, "hood_event", {"summary": event.summary, "severity": event.severity.value},
                                          room=c.room, device_identity=c.device_id)
        out["notification"] = self._notify(owner_id, event)
        return out

    # ------------------------------------------------------------------ ringing
    def _ring(self, owner_id: str, event: HoodEvent) -> Dict[str, Any]:
        cands = self.devices.callable_devices(owner_id)
        if not cands:
            return {"error": "no device with a VoIP token"}
        device = cands[0]
        briefing = compose_briefing(event)
        try:
            started = self.sessions.start_outbound_call(owner_id, device, event_id=event.event_id,
                                                        event_type=event.type, briefing=briefing)
        except VoiceError as exc:
            return {"error": str(exc)}
        call: CallRecord = started["call"]
        if self.interaction is not None:
            try:
                self.interaction.note_outbound_context("user:" + owner_id, briefing)
            except Exception:
                pass
        res = self.push.send_voip_call(device, call_id=call.call_id, join=_grant_dict(started["join"]),
                                       caller="Hood", reason=event.summary)
        if res.dead_token:
            self.devices.clear_token(device["device_id"], "voip")
        if not res.ok:
            self.store.update_call(call.call_id, notification_status=f"voip_failed:{res.status}:{res.reason}"[:80])
            self.sessions.end_call(call.call_id, EndReason.ERROR, actor="dispatcher")
            return {"error": f"VoIP push failed ({res.status} {res.reason})"}
        self.store.update_call(call.call_id, notification_status=f"voip_sent:{res.apns_id}"[:80])
        return {"call_id": call.call_id, "ringing_device": device["device_id"]}

    # ------------------------------------------------------------------ notifications and missed calls
    def _notify(self, owner_id: str, event: HoodEvent, *, title: str = "Hood") -> str:
        s = effective_settings(self.store, self.cfg, owner_id)
        if event.type == "info" and not s["notify_on_info"]:
            return "suppressed"
        sent, failed = 0, 0
        for d in self.devices.notifiable_devices(owner_id):
            r = self.push.send_alert(d, title=title, body=event.summary, collapse_id=event.subject[:60],
                                     data={"event_id": event.event_id}, time_sensitive=event.severity.rank >= EventSeverity.HIGH.rank)
            if r.dead_token:
                self.devices.clear_token(d["device_id"], "alert")
            sent += 1 if r.ok else 0
            failed += 0 if r.ok else 1
        status = "no_device" if sent + failed == 0 else f"sent:{sent},failed:{failed}"
        self.store.set_event_notification(event.event_id, status)
        return status

    def on_missed(self, call: CallRecord) -> None:
        """Nobody answered: leave a visible 'missed call' notification the owner can tap to call back."""
        if call.fallback_sent:
            return
        summary = ""
        with self.store._db() as db:
            row = db.execute("SELECT summary FROM hood_events WHERE event_id=?", (call.event_id,)).fetchone()
            summary = row["summary"] if row else "Hood tried to reach you"
        sent = 0
        for d in self.devices.notifiable_devices(call.owner_id):
            r = self.push.send_alert(d, title="Missed call from Hood", body=summary, collapse_id=call.call_id,
                                     data={"call_id": call.call_id, "event_id": call.event_id, "action": "callback"},
                                     time_sensitive=True)
            if r.dead_token:
                self.devices.clear_token(d["device_id"], "alert")
            sent += 1 if r.ok else 0
        status = f"fallback_sent:{sent}" if sent else "fallback_unavailable"
        self.store.update_call(call.call_id, fallback_sent=bool(sent), notification_status=status)
        self.sessions.events.emit(call.call_id, "missed_fallback", {"status": status})
        if call.event_id:
            self.store.set_event_notification(call.event_id, status)

    # ------------------------------------------------------------------ call back after a miss
    def callback(self, owner_id: str, device: Dict[str, Any], missed_call_id: str) -> Dict[str, Any]:
        missed = self.store.get_call(missed_call_id)
        if missed is None or missed.owner_id != owner_id:
            raise VoiceError("Unknown call")
        return self.sessions.start_owner_call(owner_id, device, context_event_id=missed.event_id,
                                              event_type=missed.event_type, briefing=missed.briefing)
