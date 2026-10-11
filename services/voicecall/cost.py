"""Voice cost control: reserve before the call, settle after, caps and alerts.

Reservations are made against Hood's existing ``CostController`` (same daily/monthly caps as every other
model call) *and* against voice-specific per-call/daily/monthly dollar and minute caps. Failed, missed
and abandoned calls are settled too: a call that connected is never recorded as free.
"""
from __future__ import annotations

import calendar
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .config import VoiceCallConfig
from .contracts import CallRecord, VoiceBudgetExceeded
from .profiles import ProviderProfile
from .store import VoiceStore

ALERT_THRESHOLDS = (0.5, 0.8, 1.0)


def _day_start(now: float) -> float:
    t = time.gmtime(now)
    return calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0))


def _month_start(now: float) -> float:
    t = time.gmtime(now)
    return calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0))


class VoiceCostTracker:
    def __init__(self, store: VoiceStore, cfg: VoiceCallConfig, cost_controller: Any = None,
                 alert_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.store, self.cfg, self.cc = store, cfg, cost_controller
        self.alert_sink = alert_sink
        self.clock = clock
        self._lock = threading.RLock()
        self._settings_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ limits (owner may only lower)
    def limits_for(self, owner_id: str) -> Dict[str, float]:
        s = self.store.get_settings(owner_id)
        def low(key: str, hard: float) -> float:
            try:
                v = float(s.get(key, hard))
            except (TypeError, ValueError):
                return hard
            return min(max(v, 0.0), hard)
        return {"per_call_usd": low("per_call_usd", self.cfg.per_call_usd),
                "daily_usd": low("daily_usd", self.cfg.daily_usd),
                "monthly_usd": low("monthly_usd", self.cfg.monthly_usd),
                "daily_minutes": low("daily_minutes", self.cfg.daily_minutes),
                "monthly_minutes": low("monthly_minutes", self.cfg.monthly_minutes),
                "max_call_seconds": low("max_call_seconds", self.cfg.max_call_seconds)}

    # ------------------------------------------------------------------ pre-flight
    def summary(self, owner_id: str) -> Dict[str, Any]:
        now = self.clock()
        day, month = self.store.totals_since(owner_id, _day_start(now)), self.store.totals_since(owner_id, _month_start(now))
        lim = self.limits_for(owner_id)
        return {"day": day, "month": month, "limits": lim,
                "day_remaining_usd": round(max(lim["daily_usd"] - day["usd"] - day["reserved_open_usd"], 0), 4),
                "month_remaining_usd": round(max(lim["monthly_usd"] - month["usd"] - month["reserved_open_usd"], 0), 4)}

    def reservation_amount(self, owner_id: str, profile: ProviderProfile, seconds: Optional[float] = None) -> float:
        lim = self.limits_for(owner_id)
        secs = min(seconds or lim["max_call_seconds"], lim["max_call_seconds"])
        worst = (profile.worst_case_usd_per_minute + self.cfg.livekit_usd_per_minute) * secs / 60.0
        return round(min(worst, lim["per_call_usd"]), 6)

    def check_can_start(self, owner_id: str, profile: ProviderProfile) -> float:
        """Raise VoiceBudgetExceeded unless a worst-case call fits every cap; return the amount to reserve."""
        with self._lock:
            amount = self.reservation_amount(owner_id, profile)
            s = self.summary(owner_id)
            lim = s["limits"]
            if amount <= 0:
                raise VoiceBudgetExceeded("Per-call budget is zero")
            if s["day"]["usd"] + s["day"]["reserved_open_usd"] + amount > lim["daily_usd"] + 1e-9:
                raise VoiceBudgetExceeded(f"Daily voice budget (${lim['daily_usd']:.2f}) would be exceeded")
            if s["month"]["usd"] + s["month"]["reserved_open_usd"] + amount > lim["monthly_usd"] + 1e-9:
                raise VoiceBudgetExceeded(f"Monthly voice budget (${lim['monthly_usd']:.2f}) would be exceeded")
            if s["day"]["minutes"] >= lim["daily_minutes"]:
                raise VoiceBudgetExceeded(f"Daily voice minutes ({lim['daily_minutes']:.0f}) used")
            if s["month"]["minutes"] >= lim["monthly_minutes"]:
                raise VoiceBudgetExceeded(f"Monthly voice minutes ({lim['monthly_minutes']:.0f}) used")
            return amount

    def reserve(self, owner_id: str, call_id: str, profile: ProviderProfile) -> Dict[str, Any]:
        """Reserve the worst case on Hood's global caps too. Returns {reservation_id, reserved_usd}."""
        with self._lock:
            amount = self.check_can_start(owner_id, profile)
            rid = ""
            if self.cc is not None:
                try:
                    rid = self.cc.reserve(f"voice_{call_id}", amount)
                except Exception as exc:          # BudgetExceededError from Hood's global caps
                    raise VoiceBudgetExceeded(f"Hood spending cap: {exc}") from None
            return {"reservation_id": rid, "reserved_usd": amount}

    # ------------------------------------------------------------------ usage and settlement
    def record_usage(self, call_id: str, *, audio_in_s: float = 0.0, audio_out_s: float = 0.0,
                     text_in_tokens: int = 0, text_out_tokens: int = 0, reasoning_usd: float = 0.0) -> None:
        for kind, amt in (("audio_in_s", audio_in_s), ("audio_out_s", audio_out_s),
                          ("text_in_tokens", text_in_tokens), ("text_out_tokens", text_out_tokens)):
            if amt:
                self.store.add_usage(call_id, kind, amt)
        if reasoning_usd:
            self.store.add_usage(call_id, "reasoning_usd", reasoning_usd, usd=reasoning_usd)

    def running_cost(self, call: CallRecord, profile: ProviderProfile, elapsed_s: float) -> float:
        """Best current estimate of what this call has cost (measured usage if any, else duration worst-case)."""
        u = self.store.usage_sum(call.call_id)
        measured = u.get("audio_in_s", 0) or u.get("audio_out_s", 0) or u.get("text_in_tokens", 0)
        if measured:
            base = profile.usd(u.get("audio_in_s", 0), u.get("audio_out_s", 0),
                               int(u.get("text_in_tokens", 0)), int(u.get("text_out_tokens", 0)))
        else:
            base = profile.worst_case_usd_per_minute * elapsed_s / 60.0
        return base + self.cfg.livekit_usd_per_minute * elapsed_s / 60.0 + u.get("reasoning_usd_usd", 0.0)

    def settle(self, call: CallRecord, profile: ProviderProfile, duration_s: float, *, connected: bool) -> Dict[str, Any]:
        """Final cost of a call. Never zero for a call that connected without measured usage."""
        with self._lock:
            u = self.store.usage_sum(call.call_id)
            measured = bool(u.get("audio_in_s") or u.get("audio_out_s") or u.get("text_in_tokens"))
            if not connected:
                cost = 0.0                      # nobody joined: only the room reservation existed
            else:
                cost = self.running_cost(call, profile, duration_s)
            if not measured and call.reserved_usd:
                cost = min(cost, call.reserved_usd)      # a duration-based estimate cannot exceed what was reserved
            cost = round(cost, 6)
            if self.cc is not None and call.reservation_id:
                try:
                    from packages.contracts import ModelUsage
                    self.cc.settle(call.reservation_id, ModelUsage(estimated_cost_usd=cost), cost_measured=True,
                                   provider="voicecall", model=profile.id)
                except Exception:
                    pass
            self.store.update_call(call.call_id, cost_usd=cost, cost_measured=measured,
                                   audio_in_s=u.get("audio_in_s", 0.0), audio_out_s=u.get("audio_out_s", 0.0))
            self._alerts(call.owner_id)
            return {"cost_usd": cost, "measured": measured}

    def release_unused(self, call: CallRecord) -> None:
        if self.cc is not None and call.reservation_id:
            try:
                self.cc.release(call.reservation_id)
            except Exception:
                pass

    # ------------------------------------------------------------------ alerts
    def _alerts(self, owner_id: str) -> None:
        if self.alert_sink is None:
            return
        s = self.summary(owner_id)
        now = self.clock()
        for period, used, cap, start in (("day", s["day"]["usd"], s["limits"]["daily_usd"], _day_start(now)),
                                         ("month", s["month"]["usd"], s["limits"]["monthly_usd"], _month_start(now))):
            if cap <= 0:
                continue
            for th in ALERT_THRESHOLDS:
                if used >= cap * th and self.store.alert_once(f"{owner_id}:{period}:{int(start)}:{th}"):
                    self.alert_sink("voice_spend", {"period": period, "threshold": th, "used_usd": round(used, 4),
                                                    "cap_usd": cap})

    def month_table(self, owner_id: str) -> Dict[str, Any]:
        s = self.summary(owner_id)
        return {"month_usd": round(s["month"]["usd"], 4), "month_minutes": round(s["month"]["minutes"], 2),
                "month_calls": s["month"]["calls"], "limits": s["limits"]}
