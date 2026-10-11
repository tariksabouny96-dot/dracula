"""LiveKit implementation of ``RealtimeTransport``.

Uses LiveKit's documented access-token format (HS256 JWT with a ``video`` grant, ``roomConfig.agents``
for explicit agent dispatch) and the RoomService Twirp-over-HTTP API. No LiveKit SDK is imported, so
Hood's core stays lean and the API secret is only ever used in this file. Every outbound call is
checked against Hood's egress firewall when one is attached.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .config import VoiceCallConfig
from .contracts import JoinGrant, VoiceError
from .tokens import jwt_hs256


class TransportError(VoiceError):
    status = 502


class LiveKitTransport:
    name = "livekit"

    def __init__(self, cfg: VoiceCallConfig, *, firewall: Any = None, timeout: float = 10.0):
        if not cfg.transport_ready:
            raise TransportError("LiveKit is not configured (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)")
        self.cfg = cfg
        self.firewall = firewall
        self.timeout = timeout
        self._key = cfg.livekit_api_key
        self._secret = cfg.livekit_api_secret.reveal()

    # ------------------------------------------------------------------ tokens
    def _token(self, identity: str, ttl: int, video: Dict[str, Any], *, name: str = "", metadata: str = "",
               attributes: Optional[Dict[str, str]] = None, room_config: Optional[Dict[str, Any]] = None) -> str:
        now = int(time.time())
        claims: Dict[str, Any] = {"iss": self._key, "sub": identity, "nbf": now - 5, "exp": now + int(ttl),
                                  "video": video, "identity": identity}
        if name:
            claims["name"] = name
        if metadata:
            claims["metadata"] = metadata
        if attributes:
            claims["attributes"] = attributes
        if room_config:
            claims["roomConfig"] = room_config
        return jwt_hs256(claims, self._secret)

    def mint_join_token(self, *, room: str, identity: str, display_name: str, ttl_seconds: int,
                        metadata: str = "", attributes: Optional[Dict[str, str]] = None,
                        dispatch_agent: bool = False, agent_metadata: str = "") -> JoinGrant:
        # Least privilege: join exactly one room, publish the microphone only, receive audio and data.
        video = {"room": room, "roomJoin": True, "canPublish": True, "canSubscribe": True,
                 "canPublishData": True, "canPublishSources": ["microphone"], "canUpdateOwnMetadata": False}
        # Room settings travel in the token, so the room (and the agent that is dispatched into it) comes into
        # being exactly when the owner's device joins and goes away shortly after everyone leaves.
        room_config: Dict[str, Any] = {"emptyTimeout": 30, "departureTimeout": 20, "maxParticipants": 3}
        if dispatch_agent:
            room_config["agents"] = [{"agentName": self.cfg.agent_name, "metadata": agent_metadata}]
        tok = self._token(identity, ttl_seconds, video, name=display_name, metadata=metadata,
                          attributes=attributes, room_config=room_config)
        return JoinGrant(url=self.cfg.livekit_url, token=tok, room=room, identity=identity,
                         expires_at=time.time() + ttl_seconds)

    # ------------------------------------------------------------------ server API (Twirp JSON)
    def _admin_token(self, room: str = "", *, create: bool = False) -> str:
        video: Dict[str, Any] = {"roomAdmin": True, "room": room} if room else {}
        if create:
            video["roomCreate"] = True
        return self._token("hood-server", 60, video)

    def _twirp(self, method: str, body: Dict[str, Any], *, room: str = "", create: bool = False) -> Dict[str, Any]:
        base = self.cfg.livekit_http_url.rstrip("/")
        url = f"{base}/twirp/livekit.RoomService/{method}"
        parts = urllib.parse.urlsplit(url)
        if self.firewall is not None:
            self.firewall.enforce(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                  purpose="voicecall:livekit")
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self._admin_token(room, create=create)})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:   # noqa: S310 (https/ws config)
                raw = resp.read(2_000_000)
        except urllib.error.HTTPError as exc:
            detail = exc.read(500).decode("utf-8", "replace")
            raise TransportError(f"LiveKit {method} failed: HTTP {exc.code} {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"LiveKit unreachable: {type(exc).__name__}") from None
        return json.loads(raw or b"{}")

    def create_room(self, room: str, *, empty_timeout: int = 60, departure_timeout: int = 20,
                    max_participants: int = 3, metadata: str = "", dispatch_agent: bool = False,
                    agent_metadata: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": room, "emptyTimeout": empty_timeout, "departureTimeout": departure_timeout,
                                "maxParticipants": max_participants, "metadata": metadata}
        if dispatch_agent:
            body["agents"] = [{"agentName": self.cfg.agent_name, "metadata": agent_metadata}]
        return self._twirp("CreateRoom", body, room=room, create=True)

    def delete_room(self, room: str) -> bool:
        try:
            self._twirp("DeleteRoom", {"room": room}, room=room, create=True)
            return True
        except TransportError as exc:
            if "not_found" in str(exc) or "404" in str(exc):
                return True            # already gone: deleting is idempotent
            raise

    def list_participants(self, room: str) -> List[Dict[str, Any]]:
        try:
            return list(self._twirp("ListParticipants", {"room": room}, room=room).get("participants", []))
        except TransportError as exc:
            if "not_found" in str(exc) or "404" in str(exc):
                return []
            raise

    def remove_participant(self, room: str, identity: str) -> bool:
        try:
            self._twirp("RemoveParticipant", {"room": room, "identity": identity}, room=room)
            return True
        except TransportError as exc:
            if "not_found" in str(exc) or "404" in str(exc):
                return False
            raise

    def send_data(self, room: str, payload: Dict[str, Any], *, destination_identities: Optional[List[str]] = None,
                  topic: str = "hood") -> bool:
        data = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
        body: Dict[str, Any] = {"room": room, "data": data, "kind": "RELIABLE", "topic": topic}
        if destination_identities:
            body["destinationIdentities"] = destination_identities
        try:
            self._twirp("SendData", body, room=room)
            return True
        except TransportError:
            return False
