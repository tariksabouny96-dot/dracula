"""Who may call whom, and when.

``CallPermissionManager``: owner-only authorization for sessions that Hood answers.
``OwnerCallPolicy``: decides whether an event may ring the owner's phone, only be a notification, or
be dropped. The recipient is never an argument: calls go to the owner's registered devices only.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from .config import CALLABLE_EVENT_TYPES, VoiceCallConfig
from .contracts import CallDirection, EventSeverity, Forbidden, HoodEvent, RateLimited, VoiceError
from .cost import VoiceCostTracker
from .devices import DeviceRegistry
from .store import VoiceStore

SUPPORTED_LANGUAGES = {"en": "English", "fr": "French", "ar": "Arabic (untested)", "ar-MA": "Moroccan Darija (untested)"}
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
OWNER_STARTS_PER_HOUR = 20


# ---------------------------------------------------------------------- owner settings
def default_settings(cfg: VoiceCallConfig) -> Dict[str, Any]:
    return {"language": cfg.language, "calls_enabled": True, "quiet_start": cfg.quiet_start, "quiet_end": cfg.quiet_end,
            "timezone": cfg.timezone, "urgent_override": cfg.critical_overrides_quiet_hours,
            "event_toggles": {t: True for t in CALLABLE_EVENT_TYPES}, "notify_on_info": True,
            "per_call_usd": cfg.per_call_usd, "daily_usd": cfg.daily_usd, "monthly_usd": cfg.monthly_usd,
            "daily_minutes": cfg.daily_minutes, "monthly_minutes": cfg.monthly_minutes,
            "max_call_seconds": cfg.max_call_seconds}


def effective_settings(store: VoiceStore, cfg: VoiceCallConfig, owner_id: str) -> Dict[str, Any]:
    merged = default_settings(cfg)
    saved = store.get_settings(owner_id)
    for k, v in saved.items():
        if k in merged:
            if k == "event_toggles" and isinstance(v, dict):
                merged[k] = {**merged[k], **{t: bool(x) for t, x in v.items() if t in CALLABLE_EVENT_TYPES}}
            else:
                merged[k] = v
    return merged


def update_settings(store: VoiceStore, cfg: VoiceCallConfig, owner_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and save. Spending and duration limits can be lowered, never raised above server config."""
    current = effective_settings(store, cfg, owner_id)
    hard = default_settings(cfg)
    for key, value in patch.items():
        if key == "language":
            if value not in SUPPORTED_LANGUAGES:
                raise VoiceError(f"language must be one of {sorted(SUPPORTED_LANGUAGES)}")
        elif key in ("calls_enabled", "urgent_override", "notify_on_info"):
            if not isinstance(value, bool):
                raise VoiceError(f"{key} must be true or false")
        elif key in ("quiet_start", "quiet_end"):
            if not isinstance(value, str) or not _HHMM.match(value):
                raise VoiceError(f"{key} must be HH:MM")
        elif key == "timezone":
            try:
                ZoneInfo(str(value))
            except Exception:
                raise VoiceError("timezone must be an IANA name such as Africa/Casablanca") from None
        elif key == "event_toggles":
            if not isinstance(value, dict) or any(t not in CALLABLE_EVENT_TYPES for t in value):
                raise VoiceError(f"event_toggles keys must be among {list(CALLABLE_EVENT_TYPES)}")
            value = {**current["event_toggles"], **{t: bool(x) for t, x in value.items()}}
        elif key in ("per_call_usd", "daily_usd", "monthly_usd", "daily_minutes", "monthly_minutes", "max_call_seconds"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise VoiceError(f"{key} must be a non-negative number")
            if value > hard[key]:
                raise VoiceError(f"{key} cannot exceed the server limit ({hard[key]})")
        else:
            raise VoiceError(f"Unknown setting: {key}")
        current[key] = value
    store.put_settings(owner_id, current)
    return current


# ---------------------------------------------------------------------- permissions
class CallPermissionManager:
    def __init__(self, store: VoiceStore, devices: DeviceRegistry, auth_service: Any = None,
                 clock: Callable[[], float] = time.time):
        self.store, self.devices, self.auth, self.clock = store, devices, auth_service, clock

    def require_owner(self, owner_id: str) -> str:
        """Return the owner's username; raise Forbidden unless this is the active ROOT_OWNER."""
        if self.auth is None:
            return "Zak"                                  # offline dev/test mode, like the rest of Hood
        user = self.auth.get_user_by_id(owner_id)
        if not user or not user.get("is_active", True) or str(user.get("role")) not in ("ROOT_OWNER", "UserRole.ROOT_OWNER"):
            raise Forbidden("Only the Root Owner can use voice calls")
        return user["username"]

    def authorize_owner_start(self, owner_id: str, device: Dict[str, Any]) -> None:
        self.require_owner(owner_id)
        if device["owner_id"] != owner_id or device.get("revoked_at"):
            raise Forbidden("Device is not authorised")
        recent = self.store.calls_since(owner_id, self.clock() - 3600, CallDirection.OWNER_TO_HOOD)
        if len(recent) >= OWNER_STARTS_PER_HOUR:
            raise RateLimited("Too many calls started in the last hour")


# ---------------------------------------------------------------------- Hood-initiated policy
@dataclass
class Decision:
    action: str                 # "call" | "notify" | "notify_in_call" | "drop"
    reason: str


def in_quiet_hours(start: str, end: str, tz: str, now: float) -> bool:
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    local = datetime.fromtimestamp(now, zone)
    cur = local.hour * 60 + local.minute
    s = int(start[:2]) * 60 + int(start[3:])
    e = int(end[:2]) * 60 + int(end[3:])
    if s == e:
        return False
    return (s <= cur < e) if s < e else (cur >= s or cur < e)


class OwnerCallPolicy:
    MIN_SEVERITY = EventSeverity.HIGH

    def __init__(self, store: VoiceStore, cfg: VoiceCallConfig, devices: DeviceRegistry, cost: VoiceCostTracker,
                 profile_getter: Callable[[], Any], clock: Callable[[], float] = time.time):
        self.store, self.cfg, self.devices, self.cost = store, cfg, devices, cost
        self.profile_getter = profile_getter
        self.clock = clock

    def evaluate(self, owner_id: str, event: HoodEvent, *, force_call: bool = False) -> Decision:
        now = self.clock()
        s = effective_settings(self.store, self.cfg, owner_id)
        prior = self.store.last_event_for_subject(event.subject, now - self.cfg.dedupe_window_seconds)
        if prior and prior.get("decision") in ("call", "notify", "notify_in_call"):
            return Decision("drop", f"duplicate of {prior['event_id']} within {self.cfg.dedupe_window_seconds}s")
        if event.type not in CALLABLE_EVENT_TYPES or not s["event_toggles"].get(event.type, False):
            return Decision("notify", "event type is not configured to call")
        if not s["calls_enabled"]:
            return Decision("notify", "calls are switched off in settings")
        if event.severity.rank < self.MIN_SEVERITY.rank and not force_call:
            return Decision("notify", f"severity {event.severity.value} is below the calling threshold")
        if self.store.active_calls(owner_id):
            return Decision("notify_in_call", "a call is already in progress")
        if in_quiet_hours(s["quiet_start"], s["quiet_end"], s["timezone"], now):
            if not (event.severity == EventSeverity.CRITICAL and s["urgent_override"]
                    and self.cfg.critical_overrides_quiet_hours):
                return Decision("notify", "quiet hours")
        outbound = self.store.calls_since(owner_id, now - 3600, CallDirection.HOOD_TO_OWNER)
        if len(outbound) >= min(self.cfg.max_calls_per_hour, 6):
            return Decision("notify", "hourly call limit reached")
        if outbound and event.severity != EventSeverity.CRITICAL:
            if now - max(c.created for c in outbound) < self.cfg.min_gap_seconds:
                return Decision("notify", "too soon after the previous call")
        if not self.devices.callable_devices(owner_id):
            return Decision("notify", "no registered device can ring (no VoIP token)")
        try:
            self.cost.check_can_start(owner_id, self.profile_getter())
        except Exception as exc:
            return Decision("notify", f"voice budget: {exc}")
        return Decision("call", "eligible")
