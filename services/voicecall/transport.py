"""Transport-neutral interface for real-time audio rooms.

Hood only needs: mint a join token, create/end a room, look at who is in it, push a small data message.
LiveKit is one implementation (livekit.py); ``InMemoryTransport`` (fake.py) is used by tests. Swapping
vendors means writing another class with these methods; nothing else in Hood changes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .contracts import JoinGrant


@runtime_checkable
class RealtimeTransport(Protocol):
    name: str

    def mint_join_token(self, *, room: str, identity: str, display_name: str, ttl_seconds: int,
                        metadata: str = "", attributes: Optional[Dict[str, str]] = None,
                        dispatch_agent: bool = False, agent_metadata: str = "") -> JoinGrant: ...

    def create_room(self, room: str, *, empty_timeout: int = 60, departure_timeout: int = 20,
                    max_participants: int = 3, metadata: str = "", dispatch_agent: bool = False,
                    agent_metadata: str = "") -> Dict[str, Any]: ...

    def delete_room(self, room: str) -> bool: ...

    def list_participants(self, room: str) -> List[Dict[str, Any]]: ...

    def remove_participant(self, room: str, identity: str) -> bool: ...

    def send_data(self, room: str, payload: Dict[str, Any], *, destination_identities: Optional[List[str]] = None,
                  topic: str = "hood") -> bool: ...
