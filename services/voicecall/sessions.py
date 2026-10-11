"""VoiceSessionManager: call lifecycle, join tokens, limits and cleanup.

The server is the authority on every limit. The worker (and the phone) are never trusted to stop a call:
a watchdog ends calls at max duration, at the spending cap, when nobody answers, when the worker goes
silent, and removes unexpected participants. Every ended call is settled, including failed and missed ones.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .config import VoiceCallConfig
from .contracts import (TRANSITIONS, CallDirection, CallRecord, CallState, Conflict, EndReason, Forbidden, JoinGrant,
                        NotConfigured, NotFound, VoiceError)
from .cost import VoiceCostTracker
from .devices import DeviceRegistry
from .events import VoiceEventDispatcher
from .policy import CallPermissionManager, effective_settings
from .profiles import ProviderProfile, get_profile
from .store import VoiceStore
from .tokens import jwt_hs256, verify_hs256  # gitleaks:allow (function names)

CONNECT_TIMEOUT_SECONDS = 90
MAX_UNBRIDGED_TURNS = 3
OWNER_ABSENT_GRACE_SECONDS = 60


class VoiceSessionManager:
    def __init__(self, store: VoiceStore, transport: Any, cost: VoiceCostTracker, perms: CallPermissionManager,
                 devices: DeviceRegistry, events: VoiceEventDispatcher, cfg: VoiceCallConfig, *,
                 clock: Callable[[], float] = time.time, stop_latch: Any = None):
        self.store, self.transport, self.cost, self.perms = store, transport, cost, perms
        self.devices, self.events, self.cfg, self.clock, self.stop_latch = devices, events, cfg, clock, stop_latch
        self.on_missed: Optional[Callable[[CallRecord], None]] = None
        self.on_ended: Optional[Callable[[CallRecord], None]] = None
        self._lock = threading.RLock()
        self._absent_since: Dict[str, float] = {}
        self._unbridged: Dict[str, int] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ helpers
    def profile(self) -> ProviderProfile:
        return get_profile(self.cfg.provider)

    def _require_ready(self) -> None:
        if not self.cfg.enabled:
            raise NotConfigured("Voice calling is switched off (HOOD_VOICECALL_ENABLED)")
        if self.transport is None:
            raise NotConfigured("No real-time transport is configured")

    def _transition(self, call: CallRecord, new: CallState, **fields: Any) -> CallRecord:
        allowed = TRANSITIONS.get(call.state, ())
        if new not in allowed:
            raise Conflict(f"Call cannot go from {call.state.value} to {new.value}")
        self.store.update_call(call.call_id, state=new, **fields)
        self.store.add_event(call.call_id, "state", {"from": call.state.value, "to": new.value})
        return self.store.get_call(call.call_id)

    def agent_token(self, call: CallRecord) -> str:
        ttl = self.cost.limits_for(call.owner_id)["max_call_seconds"] + 600
        now = int(self.clock())
        return jwt_hs256({"iss": "hood", "sub": call.call_id, "scope": "agent", "nbf": now - 5, "exp": now + int(ttl)},
                         self.cfg.agent_secret.reveal())

    def verify_agent_token(self, token: str) -> CallRecord:
        try:
            claims = verify_hs256(token, self.cfg.agent_secret.reveal(), now=self.clock())
        except ValueError:
            raise Forbidden("Invalid agent credential") from None
        if claims.get("scope") != "agent":
            raise Forbidden("Invalid agent credential")
        call = self.store.get_call(str(claims.get("sub")))
        if call is None:
            raise Forbidden("Invalid agent credential")
        return call

    def _grant(self, call: CallRecord, ttl: int, device_id: str) -> JoinGrant:
        meta = json.dumps({"call_id": call.call_id, "agent_token": self.agent_token(call), "mode": call.mode,
                           "language": call.language, "provider": call.provider}, separators=(",", ":"))
        return self.transport.mint_join_token(room=call.room, identity=device_id, display_name="Owner",
                                              ttl_seconds=ttl, dispatch_agent=True, agent_metadata=meta,
                                              attributes={"role": "owner", "call_id": call.call_id})

    def _new_call(self, owner_id: str, direction: CallDirection, state: CallState, device_id: str, language: str,
                  event_id: str = "", event_type: str = "", briefing: str = "") -> CallRecord:
        profile = self.profile()
        call_id = "call_" + secrets.token_hex(10)
        res = self.cost.reserve(owner_id, call_id, profile)
        call = CallRecord(call_id=call_id, owner_id=owner_id, direction=direction, state=CallState.CREATED,
                          room=call_id, device_id=device_id, created=self.clock(), provider=profile.id,
                          mode=profile.mode, language=language, event_id=event_id, event_type=event_type,
                          briefing=briefing, reservation_id=res["reservation_id"], reserved_usd=res["reserved_usd"])
        self.store.insert_call(call)
        self.store.add_event(call_id, "created", {"direction": direction.value, "provider": profile.id})
        return call

    # ------------------------------------------------------------------ owner calls Hood
    def start_owner_call(self, owner_id: str, device: Dict[str, Any], language: Optional[str] = None, *,
                         context_event_id: str = "", event_type: str = "", briefing: str = "") -> Dict[str, Any]:
        with self._lock:
            self._require_ready()
            self.perms.authorize_owner_start(owner_id, device)
            for c in self.store.active_calls(owner_id):
                if c.device_id == device["device_id"] and c.state in (CallState.CONNECTING, CallState.ACTIVE) \
                        and c.direction == CallDirection.OWNER_TO_HOOD:
                    return self._rejoin_locked(c, device["device_id"])          # tapped Call twice: same call
                raise Conflict("Another call is already in progress")
            lang = language or effective_settings(self.store, self.cfg, owner_id)["language"]
            call = self._new_call(owner_id, CallDirection.OWNER_TO_HOOD, CallState.CREATED, device["device_id"], lang,
                                  event_id=context_event_id, event_type=event_type, briefing=briefing)
            call = self._transition(call, CallState.CONNECTING, answered_at=self.clock())
            grant = self._grant(call, self.cfg.join_ttl_owner_seconds, device["device_id"])
            self.events.emit(call.call_id, "call_started", {"language": lang})
            return {"call": call.public(), "join": _grant_dict(grant), "limits": self.cost.limits_for(owner_id)}

    # ------------------------------------------------------------------ Hood calls owner
    def start_outbound_call(self, owner_id: str, device: Dict[str, Any], *, event_id: str, event_type: str,
                            briefing: str, language: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            self._require_ready()
            self.perms.require_owner(owner_id)
            if device["owner_id"] != owner_id or device.get("revoked_at") or not device.get("voip_token"):
                raise Forbidden("Device cannot receive calls")
            if self.store.active_calls(owner_id):
                raise Conflict("Another call is already in progress")
            lang = language or effective_settings(self.store, self.cfg, owner_id)["language"]
            call = self._new_call(owner_id, CallDirection.HOOD_TO_OWNER, CallState.CREATED, device["device_id"], lang,
                                  event_id=event_id, event_type=event_type, briefing=briefing)
            call = self._transition(call, CallState.RINGING, ringing_at=self.clock())
            grant = self._grant(call, self.cfg.join_ttl_incoming_seconds, device["device_id"])
            self.events.emit(call.call_id, "ringing", {"event_type": event_type})
            return {"call": call, "join": grant}

    def answer(self, call_id: str, device_id: str) -> Dict[str, Any]:
        with self._lock:
            call = self._owned(call_id, device_id)
            if call.direction != CallDirection.HOOD_TO_OWNER:
                raise Conflict("Not an incoming call")
            if call.state == CallState.CONNECTING:
                return self._rejoin_locked(call, device_id)
            call = self._transition(call, CallState.CONNECTING, answered_at=self.clock())
            grant = self._grant(call, self.cfg.join_ttl_owner_seconds, device_id)
            self.events.emit(call_id, "answered", {})
            return {"call": call.public(), "join": _grant_dict(grant), "limits": self.cost.limits_for(call.owner_id)}

    def decline(self, call_id: str, device_id: str) -> Dict[str, Any]:
        with self._lock:
            call = self._owned(call_id, device_id)
            call = self._transition(call, CallState.DECLINED, ended_at=self.clock(), end_reason=EndReason.DECLINED.value)
            self._finish_cost(call, connected=False)
            self.events.emit(call_id, "declined", {})
            self._notify_ended(call)
            return {"call": self.store.get_call(call_id).public()}

    # ------------------------------------------------------------------ in-call
    def _owned(self, call_id: str, device_id: str) -> CallRecord:
        call = self.store.get_call(call_id)
        if call is None or call.device_id != device_id:
            raise NotFound("Unknown call")
        return call

    def rejoin(self, call_id: str, device_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._rejoin_locked(self._owned(call_id, device_id), device_id)

    def _rejoin_locked(self, call: CallRecord, device_id: str) -> Dict[str, Any]:
        if call.state not in (CallState.CONNECTING, CallState.ACTIVE):
            raise Conflict(f"Call is {call.state.value}")
        grant = self._grant(call, self.cfg.join_ttl_owner_seconds, device_id)
        self.events.emit(call.call_id, "rejoin", {})
        return {"call": call.public(), "join": _grant_dict(grant), "limits": self.cost.limits_for(call.owner_id)}

    def end_call(self, call_id: str, reason: EndReason = EndReason.OWNER_HANGUP, *, actor: str = "") -> Optional[CallRecord]:
        with self._lock:
            call = self.store.get_call(call_id)
            if call is None:
                raise NotFound("Unknown call")
            if call.state.terminal:
                return call                                            # idempotent
            connected = call.state == CallState.ACTIVE or call.last_heartbeat is not None
            # A call that never carried audio and ended for a technical reason is FAILED; otherwise ENDED.
            new = (CallState.FAILED if (not connected and call.state == CallState.CONNECTING and
                                        reason in (EndReason.WORKER_LOST, EndReason.NETWORK, EndReason.ERROR))
                   else CallState.ENDED)
            now = self.clock()
            started = call.answered_at or call.created
            duration = max(0.0, now - started) if connected else 0.0
            self.store.update_call(call_id, duration_s=duration)
            call = self._transition(call, new, ended_at=now, end_reason=reason.value, duration_s=duration)
            try:
                self.transport.delete_room(call.room)
            except Exception as exc:
                self.store.add_event(call_id, "room_cleanup_failed", {"error": type(exc).__name__})
            self._finish_cost(call, connected=connected)
            self.events.emit(call_id, "ended", {"reason": reason.value, "actor": actor, "duration_s": round(duration, 1)})
            self._notify_ended(call)
            return self.store.get_call(call_id)

    def _finish_cost(self, call: CallRecord, *, connected: bool) -> None:
        fresh = self.store.get_call(call.call_id) or call
        self.cost.settle(fresh, self.profile(), fresh.duration_s, connected=connected)

    def _notify_ended(self, call: CallRecord) -> None:
        if self.on_ended:
            try:
                self.on_ended(self.store.get_call(call.call_id))
            except Exception:
                pass

    def confirm_connected(self, call_id: str, device_id: str) -> Dict[str, Any]:
        """The phone says it joined; verify with the transport before trusting it."""
        with self._lock:
            call = self._owned(call_id, device_id)
            if call.state == CallState.ACTIVE:
                return {"state": call.state.value}
            if call.state != CallState.CONNECTING:
                raise Conflict(f"Call is {call.state.value}")
            present = {p.get("identity") for p in self.transport.list_participants(call.room)}
            if device_id not in present:
                raise Conflict("Not in the room yet")
            call = self._transition(call, CallState.ACTIVE, last_heartbeat=self.clock())
            self.events.emit(call_id, "connected", {})
            return {"state": call.state.value}

    # ------------------------------------------------------------------ worker side
    def agent_heartbeat(self, call: CallRecord, usage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            call = self.store.get_call(call.call_id)
            usage = usage or {}
            if call.state.terminal:
                return {"continue": False, "reason": call.end_reason or call.state.value.lower()}
            ub = int(usage.get("unbridged_turns", 0) or 0)
            if ub > self._unbridged.get(call.call_id, 0):
                self._unbridged[call.call_id] = ub
                self.store.add_event(call.call_id, "unbridged_speech", {"count": ub})
            if ub >= MAX_UNBRIDGED_TURNS:                 # the voice model keeps speaking words Hood did not give it
                self.end_call(call.call_id, EndReason.ERROR, actor="integrity")
                return {"continue": False, "reason": "voice_integrity"}
            if usage:
                self.cost.record_usage(call.call_id, audio_in_s=float(usage.get("audio_in_s", 0) or 0),
                                       audio_out_s=float(usage.get("audio_out_s", 0) or 0),
                                       text_in_tokens=int(usage.get("text_in_tokens", 0) or 0),
                                       text_out_tokens=int(usage.get("text_out_tokens", 0) or 0))
            now = self.clock()
            fields: Dict[str, Any] = {"last_heartbeat": now}
            if call.state == CallState.RINGING and call.device_id:
                # The owner's device joined the room using the join token from the ring (it could not reach Hood's
                # API to call answer()): being in the room IS answering. Only the registered device holds that token.
                try:
                    present = {p.get("identity") for p in self.transport.list_participants(call.room)}
                except Exception:
                    present = set()
                if call.device_id in present:
                    call = self._transition(call, CallState.CONNECTING, answered_at=now)
                    self.events.emit(call.call_id, "answered", {"via": "room_join"})
            if call.state == CallState.CONNECTING:
                call = self._transition(call, CallState.ACTIVE, **fields)
                self.events.emit(call.call_id, "connected", {"via": "agent"})
            else:
                self.store.update_call(call.call_id, **fields)
            return self._limits_verdict(self.store.get_call(call.call_id))

    def _limits_verdict(self, call: CallRecord) -> Dict[str, Any]:
        now = self.clock()
        lim = self.cost.limits_for(call.owner_id)
        started = call.answered_at or call.created
        elapsed = now - started
        running = self.cost.running_cost(call, self.profile(), elapsed)
        cap = min(call.reserved_usd or lim["per_call_usd"], lim["per_call_usd"])
        remaining_s = lim["max_call_seconds"] - elapsed
        verdict = {"continue": True, "reason": "", "remaining_seconds": max(0, int(remaining_s)),
                   "remaining_usd": round(max(cap - running, 0.0), 4)}
        if remaining_s <= 0:
            verdict.update({"continue": False, "reason": EndReason.MAX_DURATION.value})
        elif running >= cap:
            verdict.update({"continue": False, "reason": EndReason.BUDGET.value})
        return verdict

    # ------------------------------------------------------------------ watchdog
    def sweep(self) -> List[str]:
        """One pass of enforcement. Returns descriptions of what it did (for tests/logs)."""
        done: List[str] = []
        with self._lock:
            now = self.clock()
            for call in self.store.active_calls():
                try:
                    done.extend(self._sweep_one(call, now))
                except Exception as exc:
                    self.store.add_event(call.call_id, "sweep_error", {"error": type(exc).__name__})
        return done

    def _sweep_one(self, call: CallRecord, now: float) -> List[str]:
        out: List[str] = []
        if call.state == CallState.RINGING and now - (call.ringing_at or call.created) > self.cfg.ring_timeout_seconds:
            c = self._transition(call, CallState.MISSED, ended_at=now, end_reason=EndReason.NO_ANSWER.value)
            self._finish_cost(c, connected=False)
            self.events.emit(c.call_id, "missed", {})
            out.append(f"missed:{c.call_id}")
            if self.on_missed:
                self.on_missed(self.store.get_call(c.call_id))
            self._notify_ended(c)
        elif call.state == CallState.CONNECTING and now - (call.answered_at or call.created) > CONNECT_TIMEOUT_SECONDS:
            self.end_call(call.call_id, EndReason.NETWORK, actor="watchdog")
            out.append(f"connect-timeout:{call.call_id}")
        elif call.state == CallState.ACTIVE:
            verdict = self._limits_verdict(call)
            if not verdict["continue"]:
                self.end_call(call.call_id, EndReason(verdict["reason"]), actor="watchdog")
                out.append(f"{verdict['reason']}:{call.call_id}")
            elif call.last_heartbeat and now - call.last_heartbeat > self.cfg.heartbeat_stale_seconds:
                self.end_call(call.call_id, EndReason.WORKER_LOST, actor="watchdog")
                out.append(f"worker-lost:{call.call_id}")
            else:
                out.extend(self._enforce_participants(call, now))
        return out

    def _enforce_participants(self, call: CallRecord, now: float) -> List[str]:
        out: List[str] = []
        try:
            people = [p.get("identity") for p in self.transport.list_participants(call.room)]
        except Exception:
            return out
        for ident in people:
            if ident != call.device_id and not str(ident).startswith("agent"):
                self.transport.remove_participant(call.room, ident)
                self.store.add_event(call.call_id, "unexpected_participant_removed", {"identity": str(ident)[:40]})
                out.append(f"removed:{ident}")
        if call.device_id not in people:
            gone = self._absent_since.setdefault(call.call_id, now)
            if now - gone > OWNER_ABSENT_GRACE_SECONDS:
                self.end_call(call.call_id, EndReason.NETWORK, actor="watchdog")
                out.append(f"owner-absent:{call.call_id}")
        else:
            self._absent_since.pop(call.call_id, None)
        return out

    def recover(self) -> int:
        """After a restart, settle and close any call left open by the previous process."""
        n = 0
        for call in self.store.active_calls():
            try:
                self.end_call(call.call_id, EndReason.ERROR, actor="recovery")
            except Exception:
                pass
            n += 1
        return n

    def start(self, interval: float = 5.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(interval):
                self.sweep()
        self._thread = threading.Thread(target=loop, name="voicecall-watchdog", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def _grant_dict(g: JoinGrant) -> Dict[str, Any]:
    return {"url": g.url, "token": g.token, "room": g.room, "identity": g.identity, "expires_at": g.expires_at}
