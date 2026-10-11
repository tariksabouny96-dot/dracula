"""Voice-call configuration.

Secrets come from the environment or from ``<NAME>_FILE`` (a file holding the value), never from
source. ``Secret`` redacts itself in ``repr``/``str`` so a logged config cannot leak a key.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional


class Secret:
    __slots__ = ("_v",)

    def __init__(self, value: str = ""):
        self._v = value or ""

    def reveal(self) -> str:
        return self._v

    def __bool__(self) -> bool:
        return bool(self._v)

    def __repr__(self) -> str:
        return "Secret(<set>)" if self._v else "Secret(<unset>)"

    __str__ = __repr__


def _env(name: str, default: str = "") -> str:
    """Value of VAR, or the contents of the file named by VAR_FILE."""
    path = os.environ.get(name + "_FILE", "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return default
    return os.environ.get(name, default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


def _num(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


# Event types that may ring the owner. Anything else is a notification at most.
CALLABLE_EVENT_TYPES = ("security_critical", "operational_failure", "approval_required", "decision_blocking")

# Hard ceilings: owner settings (changed from the phone) may lower these, never raise them.
HARD_MAX_CALL_SECONDS = 3600
HARD_MAX_CALLS_PER_HOUR = 6


@dataclass
class VoiceCallConfig:
    enabled: bool = False
    # transport (LiveKit)
    livekit_url: str = ""                       # wss://<project>.livekit.cloud or ws://127.0.0.1:7880
    livekit_api_key: str = field(default="", repr=False)
    livekit_api_secret: Secret = field(default_factory=Secret)
    agent_name: str = "hood-voice"
    # speech provider profile (its mode, realtime or cascade, comes from the profile)
    provider: str = "gemini-live"               # profile id: gemini-live | gemini-cascade | openai-realtime
    language: str = "en"
    # calls
    max_call_seconds: int = 900
    ring_timeout_seconds: int = 40
    join_ttl_incoming_seconds: int = 120
    join_ttl_owner_seconds: int = 600
    reconnect_grace_seconds: int = 60
    heartbeat_stale_seconds: int = 45
    max_turns_per_call: int = 300
    max_turns_per_minute: int = 30
    # spending (USD) and minutes
    per_call_usd: float = 1.0
    daily_usd: float = 3.0
    monthly_usd: float = 15.0
    daily_minutes: int = 60
    monthly_minutes: int = 300
    livekit_usd_per_minute: float = 0.001       # conservative participant-minute estimate; plan-dependent
    # Hood-initiated calls
    max_calls_per_hour: int = 3
    min_gap_seconds: int = 600
    dedupe_window_seconds: int = 900
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    timezone: str = "UTC"
    critical_overrides_quiet_hours: bool = True
    callable_events: tuple = CALLABLE_EVENT_TYPES
    # authority limits (X activation and approvals are never available by voice; see confirm.py)
    mission_budget_cap_usd: float = 1.0
    # APNs
    apns_team_id: str = field(default="", repr=False)
    apns_key_id: str = field(default="", repr=False)
    apns_key_path: str = field(default="", repr=False)
    apns_bundle_id: str = ""
    apns_sandbox: bool = True
    # internal HMAC for per-call agent tokens (generated and stored 0600 if absent)
    agent_secret: Secret = field(default_factory=Secret)

    # ---------------------------------------------------------------- derived
    @property
    def livekit_http_url(self) -> str:
        u = self.livekit_url
        if u.startswith("wss://"):
            return "https://" + u[6:]
        if u.startswith("ws://"):
            return "http://" + u[5:]
        return u

    @property
    def transport_ready(self) -> bool:
        return bool(self.livekit_url and self.livekit_api_key and self.livekit_api_secret)

    @property
    def apns_ready(self) -> bool:
        return bool(self.apns_team_id and self.apns_key_id and self.apns_key_path and self.apns_bundle_id)

    def public_view(self) -> Dict[str, Any]:
        """Safe to return over the API / write to logs: no secrets."""
        out: Dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = repr(v) if isinstance(v, Secret) else v
        out["livekit_api_key"] = "<set>" if self.livekit_api_key else ""
        out["apns_key_path"] = "<set>" if self.apns_key_path else ""
        return out

    @classmethod
    def from_env(cls, data_dir: Path) -> "VoiceCallConfig":
        data_dir = Path(data_dir)
        cfg = cls(
            enabled=_flag("HOOD_VOICECALL_ENABLED"),
            livekit_url=_env("LIVEKIT_URL"),
            livekit_api_key=_env("LIVEKIT_API_KEY"),
            livekit_api_secret=Secret(_env("LIVEKIT_API_SECRET")),
            agent_name=_env("HOOD_VOICECALL_AGENT_NAME", "hood-voice") or "hood-voice",
            provider=_env("HOOD_VOICECALL_PROVIDER", "gemini-live"),
            language=_env("HOOD_VOICECALL_LANGUAGE", "en"),
            max_call_seconds=min(int(_num("HOOD_VOICECALL_MAX_SECONDS", 900)), HARD_MAX_CALL_SECONDS),
            per_call_usd=_num("HOOD_VOICECALL_PER_CALL_USD", 1.0),
            daily_usd=_num("HOOD_VOICECALL_DAILY_USD", 3.0),
            monthly_usd=_num("HOOD_VOICECALL_MONTHLY_USD", 15.0),
            daily_minutes=int(_num("HOOD_VOICECALL_DAILY_MINUTES", 60)),
            monthly_minutes=int(_num("HOOD_VOICECALL_MONTHLY_MINUTES", 300)),
            livekit_usd_per_minute=_num("HOOD_VOICECALL_LIVEKIT_USD_PER_MIN", 0.001),
            max_calls_per_hour=min(int(_num("HOOD_VOICECALL_MAX_CALLS_PER_HOUR", 3)), HARD_MAX_CALLS_PER_HOUR),
            quiet_start=_env("HOOD_VOICECALL_QUIET_START", "22:00"),
            quiet_end=_env("HOOD_VOICECALL_QUIET_END", "07:00"),
            timezone=_env("HOOD_VOICECALL_TZ", "UTC") or "UTC",
            mission_budget_cap_usd=_num("HOOD_VOICECALL_MISSION_BUDGET_USD", 1.0),
            apns_team_id=_env("APNS_TEAM_ID"),
            apns_key_id=_env("APNS_KEY_ID"),
            apns_key_path=_env("APNS_KEY_PATH"),
            apns_bundle_id=_env("APNS_BUNDLE_ID"),
            apns_sandbox=_flag("APNS_SANDBOX", True),
        )
        cfg.agent_secret = Secret(_env("HOOD_VOICECALL_AGENT_SECRET") or _persisted_secret(data_dir))
        return cfg


def _persisted_secret(data_dir: Path) -> str:
    """A random per-installation HMAC key, created once with 0600 permissions."""
    d = data_dir / "voicecall"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = d / ".agent_secret"
    if p.exists():
        try:
            val = p.read_text(encoding="utf-8").strip()
            if len(val) >= 32:
                return val
        except OSError:
            pass
    val = secrets.token_urlsafe(48)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(val)
    return val
