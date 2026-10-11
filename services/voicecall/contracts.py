"""Data contracts for voice calls (provider- and vendor-neutral)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CallDirection(str, Enum):
    OWNER_TO_HOOD = "owner_to_hood"
    HOOD_TO_OWNER = "hood_to_owner"


class CallState(str, Enum):
    CREATED = "CREATED"
    RINGING = "RINGING"          # Hood-initiated: VoIP push sent, waiting for the owner
    CONNECTING = "CONNECTING"    # owner accepted / pressed Call; waiting for media
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    MISSED = "MISSED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in (CallState.ENDED, CallState.MISSED, CallState.DECLINED, CallState.FAILED, CallState.EXPIRED)


# Allowed transitions; anything else is a bug and raises.
TRANSITIONS: Dict[CallState, tuple] = {
    CallState.CREATED: (CallState.RINGING, CallState.CONNECTING, CallState.FAILED, CallState.ENDED),
    CallState.RINGING: (CallState.CONNECTING, CallState.MISSED, CallState.DECLINED, CallState.FAILED, CallState.ENDED),
    CallState.CONNECTING: (CallState.ACTIVE, CallState.FAILED, CallState.ENDED, CallState.EXPIRED),
    CallState.ACTIVE: (CallState.ENDED, CallState.FAILED, CallState.EXPIRED),
}


class EndReason(str, Enum):
    OWNER_HANGUP = "owner_hangup"
    HOOD_HANGUP = "hood_hangup"
    MAX_DURATION = "max_duration"
    BUDGET = "budget"
    NO_ANSWER = "no_answer"
    DECLINED = "declined"
    WORKER_LOST = "worker_lost"
    NETWORK = "network"
    EMERGENCY = "emergency"
    ERROR = "error"
    REVOKED = "device_revoked"


class EventSeverity(str, Enum):
    INFO = "info"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return ("info", "medium", "high", "critical").index(self.value)


@dataclass
class HoodEvent:
    """Something that may deserve the owner's attention (security, failure, approval...)."""
    type: str                                   # security_critical | operational_failure | approval_required | decision_blocking | info
    severity: EventSeverity
    subject: str                                # stable key for dedupe, e.g. "approval:appr_123"
    summary: str                                # short, spoken-safe (no secrets)
    detail: str = ""                            # briefing text Hood speaks when the call connects
    source: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    event_id: str = ""


@dataclass
class JoinGrant:
    url: str
    token: str
    room: str
    identity: str
    expires_at: float


@dataclass
class CallRecord:
    call_id: str
    owner_id: str
    direction: CallDirection
    state: CallState
    room: str
    device_id: str
    created: float
    provider: str = ""
    mode: str = ""
    language: str = "en"
    event_id: str = ""
    event_type: str = ""
    briefing: str = ""
    ringing_at: Optional[float] = None
    answered_at: Optional[float] = None
    ended_at: Optional[float] = None
    end_reason: str = ""
    reservation_id: str = ""
    reserved_usd: float = 0.0
    cost_usd: float = 0.0
    cost_measured: bool = False
    duration_s: float = 0.0
    audio_in_s: float = 0.0
    audio_out_s: float = 0.0
    turns: int = 0
    last_heartbeat: Optional[float] = None
    fallback_sent: bool = False
    notification_status: str = ""

    def public(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["direction"] = self.direction.value
        d["state"] = self.state.value
        d.pop("reservation_id", None)
        return d


@dataclass
class TurnResult:
    speech: str                                 # what Hood should say aloud (already speech-cleaned)
    end_call: bool = False
    needs_confirmation: List[Dict[str, Any]] = field(default_factory=list)
    mission_id: Optional[str] = None
    blocked: bool = False
    reason: str = ""


class VoiceError(Exception):
    """Base class; ``status`` maps to an HTTP status in the API layer."""
    status = 400


class NotConfigured(VoiceError):
    status = 503


class Forbidden(VoiceError):
    status = 403


class NotFound(VoiceError):
    status = 404


class Conflict(VoiceError):
    status = 409


class RateLimited(VoiceError):
    status = 429


class VoiceBudgetExceeded(VoiceError):
    status = 402
