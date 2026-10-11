"""VoiceEventDispatcher: one place where call events are recorded and delivered.

Every event is appended to the call's audit trail (so the phone can poll it) and, best effort, pushed
over the room's data channel to the owner's device for instant UI updates (transcript lines,
confirmation requests, call-state changes).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .store import VoiceStore

log = logging.getLogger("hood.voicecall")


class VoiceEventDispatcher:
    def __init__(self, store: VoiceStore, transport_getter: Callable[[], Any]):
        self.store = store
        self._transport = transport_getter
        self.listeners: List[Callable[[str, str, Dict[str, Any]], None]] = []

    def emit(self, call_id: str, kind: str, detail: Optional[Dict[str, Any]] = None, *, room: str = "",
             device_identity: str = "") -> int:
        detail = detail or {}
        event_id = self.store.add_event(call_id, kind, detail)
        if room and device_identity:
            try:
                t = self._transport()
                if t is not None:
                    t.send_data(room, {"type": kind, "call_id": call_id, "event_id": event_id, **detail},
                                destination_identities=[device_identity], topic="hood.event")
            except Exception as exc:          # best effort: the phone also polls
                log.warning("voicecall data-channel delivery failed (%s)", type(exc).__name__)
        for fn in list(self.listeners):
            try:
                fn(call_id, kind, detail)
            except Exception:
                pass
        return event_id
