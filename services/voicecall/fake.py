"""In-memory ``RealtimeTransport`` for tests. SIMULATED: no media, no network."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from .contracts import JoinGrant


class InMemoryTransport:
    name = "in-memory"

    def __init__(self, url: str = "wss://fake.livekit.test"):
        self.url = url
        self.rooms: Dict[str, Dict[str, Any]] = {}
        self.participants: Dict[str, List[str]] = {}
        self.sent: List[Dict[str, Any]] = []
        self.minted: List[Dict[str, Any]] = []
        self.fail_create = False
        self.fail_delete = False

    def mint_join_token(self, *, room, identity, display_name, ttl_seconds, metadata="", attributes=None,
                        dispatch_agent=False, agent_metadata="") -> JoinGrant:
        rec = {"room": room, "identity": identity, "ttl": ttl_seconds, "dispatch_agent": dispatch_agent}
        self.minted.append(rec)
        return JoinGrant(url=self.url, token=f"fake.{room}.{identity}.{len(self.minted)}", room=room,
                         identity=identity, expires_at=time.time() + ttl_seconds)

    def create_room(self, room, *, empty_timeout=60, departure_timeout=20, max_participants=3, metadata="",
                    dispatch_agent=False, agent_metadata="") -> Dict[str, Any]:
        if self.fail_create:
            from .livekit import TransportError
            raise TransportError("create failed (simulated)")
        self.rooms[room] = {"name": room, "dispatch_agent": dispatch_agent, "metadata": metadata}
        self.participants.setdefault(room, [])
        return self.rooms[room]

    def delete_room(self, room: str) -> bool:
        if self.fail_delete:
            from .livekit import TransportError
            raise TransportError("delete failed (simulated)")
        self.rooms.pop(room, None)
        self.participants.pop(room, None)
        return True

    def list_participants(self, room: str) -> List[Dict[str, Any]]:
        return [{"identity": i} for i in self.participants.get(room, [])]

    def remove_participant(self, room: str, identity: str) -> bool:
        if identity in self.participants.get(room, []):
            self.participants[room].remove(identity)
            return True
        return False

    def send_data(self, room, payload, *, destination_identities=None, topic="hood") -> bool:
        self.sent.append({"room": room, "payload": json.loads(json.dumps(payload)), "to": destination_identities, "topic": topic})
        return True

    # test helper
    def join(self, room: str, identity: str) -> None:
        self.participants.setdefault(room, []).append(identity)
