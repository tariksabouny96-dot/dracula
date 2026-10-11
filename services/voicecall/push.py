"""PushNotificationService: APNs delivery for incoming calls (VoIP) and fallback alerts.

Two kinds of push, never confused:
- ``voip``: the only thing that makes the iPhone ring. PushKit delivers it and the app MUST report a
  call to CallKit immediately, or iOS throttles/terminates it (Apple's rule). Used only for real calls.
- ``alert``: an ordinary visible notification. Used as the *fallback* when a call is missed, or for
  events that do not warrant ringing. It is labelled as a notification, never presented as a call.

APNs requires HTTP/2 and an ES256 provider token (iss=Team ID, kid=Key ID, refreshed every 20-60 min).
The HTTP transport is injectable so tests run against a local HTTP/2 server or a fake. [Apple's own
documentation pages could not be fetched here; header names and limits follow Apple's long-standing
published rules and must be re-checked against the live docs before release.]
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

from .config import VoiceCallConfig
from .contracts import VoiceError
from .tokens import jwt_es256, load_p8_private_key

PROD_HOST = "api.push.apple.com"
SANDBOX_HOST = "api.sandbox.push.apple.com"
MAX_VOIP_PAYLOAD = 5120
MAX_ALERT_PAYLOAD = 4096
TOKEN_REFRESH_SECONDS = 40 * 60            # inside Apple's 20..60 minute window
# Reasons after which the stored device token is useless and must be dropped.
DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


@dataclass
class PushResult:
    ok: bool
    status: int
    reason: str = ""
    apns_id: str = ""
    dead_token: bool = False


class ApnsTransport(Protocol):
    def post(self, host: str, path: str, headers: Dict[str, str], body: bytes) -> "tuple[int, Dict[str, str], bytes]": ...


class HttpxApnsTransport:
    """Real HTTP/2 transport (httpx + h2). ``base_url`` override is for local test servers."""

    def __init__(self, *, base_url: Optional[str] = None, verify: Any = True, timeout: float = 10.0):
        import httpx
        self._httpx = httpx
        self.base_url, self.verify, self.timeout = base_url, verify, timeout
        self._clients: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def _client(self, host: str):
        with self._lock:
            c = self._clients.get(host)
            if c is None:
                c = self._httpx.Client(http2=True, http1=False if self.base_url else True, verify=self.verify,
                                       timeout=self.timeout, base_url=self.base_url or f"https://{host}")
                self._clients[host] = c
            return c

    def post(self, host, path, headers, body):
        r = self._client(host).post(path, headers=headers, content=body)
        return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content


class PushNotificationService:
    def __init__(self, cfg: VoiceCallConfig, *, transport: Optional[ApnsTransport] = None, firewall: Any = None,
                 clock: Callable[[], float] = time.time, key_pem: Optional[bytes] = None):
        self.cfg, self.firewall, self.clock = cfg, firewall, clock
        self._transport = transport
        self._key_pem = key_pem
        self._key = None
        self._jwt = ("", 0.0)
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.cfg.apns_ready or (self._key_pem is not None and bool(self.cfg.apns_team_id and self.cfg.apns_key_id
                                                                          and self.cfg.apns_bundle_id))

    # ------------------------------------------------------------------ auth token
    def _provider_token(self) -> str:
        with self._lock:
            tok, made = self._jwt
            now = self.clock()
            if tok and now - made < TOKEN_REFRESH_SECONDS:
                return tok
            if self._key is None:
                pem = self._key_pem if self._key_pem is not None else Path(self.cfg.apns_key_path).read_bytes()
                self._key = load_p8_private_key(pem)
            tok = jwt_es256({"iss": self.cfg.apns_team_id, "iat": int(now)}, self._key, {"kid": self.cfg.apns_key_id})
            self._jwt = (tok, now)
            return tok

    # ------------------------------------------------------------------ sending
    def _send(self, token: str, *, env: str, push_type: str, topic: str, payload: Dict[str, Any], priority: int,
              expiration: int, collapse_id: str = "") -> PushResult:
        if not self.configured:
            return PushResult(False, 0, "APNs is not configured")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        limit = MAX_VOIP_PAYLOAD if push_type == "voip" else MAX_ALERT_PAYLOAD
        if len(body) > limit:
            raise VoiceError(f"Push payload is {len(body)} bytes; APNs allows {limit}")
        host = SANDBOX_HOST if env == "sandbox" else PROD_HOST
        if self.firewall is not None:
            self.firewall.enforce(host, 443, purpose="voicecall:apns")
        headers = {"authorization": "bearer " + self._provider_token(), "apns-push-type": push_type,
                   "apns-topic": topic, "apns-priority": str(priority), "apns-expiration": str(expiration),
                   "content-type": "application/json"}
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id[:64]
        if self._transport is None:
            self._transport = HttpxApnsTransport()
        try:
            status, resp_headers, content = self._transport.post(host, f"/3/device/{token}", headers, body)
        except Exception as exc:
            return PushResult(False, 0, f"transport error: {type(exc).__name__}")
        reason = ""
        if status != 200:
            try:
                reason = json.loads(content or b"{}").get("reason", "")
            except ValueError:
                reason = ""
        return PushResult(status == 200, status, reason, resp_headers.get("apns-id", ""),
                          dead_token=status in (400, 410) and reason in DEAD_TOKEN_REASONS)

    def send_voip_call(self, device: Dict[str, Any], *, call_id: str, join: Dict[str, Any], caller: str = "Hood",
                       reason: str = "") -> PushResult:
        """Ring the phone. Delivered by PushKit; the app must report it to CallKit at once.

        The payload carries a short-lived, room-scoped join token so the call can connect even if the phone
        cannot reach Hood's private API at that instant. ``expiration`` is the ring window: a stale ring is
        discarded rather than delivered late.
        """
        payload = {"aps": {"content-available": 1}, "type": "hood.incoming_call", "call_id": call_id, "caller": caller,
                   "reason": reason[:140], "join": join}
        return self._send(device["voip_token"], env=device.get("apns_env", "sandbox"), push_type="voip",
                          topic=f"{self.cfg.apns_bundle_id}.voip", payload=payload, priority=10,
                          expiration=int(self.clock()) + self.cfg.ring_timeout_seconds, collapse_id=call_id)

    def send_alert(self, device: Dict[str, Any], *, title: str, body: str, collapse_id: str = "",
                   data: Optional[Dict[str, Any]] = None, time_sensitive: bool = False) -> PushResult:
        aps: Dict[str, Any] = {"alert": {"title": title[:80], "body": body[:200]}, "sound": "default"}
        if time_sensitive:
            aps["interruption-level"] = "time-sensitive"
        payload = {"aps": aps, "type": "hood.notification", **(data or {})}
        return self._send(device["alert_token"], env=device.get("apns_env", "sandbox"), push_type="alert",
                          topic=self.cfg.apns_bundle_id, payload=payload, priority=10 if time_sensitive else 5,
                          expiration=int(self.clock()) + 3600, collapse_id=collapse_id)
