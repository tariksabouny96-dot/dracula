"""
HOOD Distributed Event Bus
Coordinates system events across nodes (NODE_ONLINE, EMERGENCY_STOP, TASK_COMPLETED, MEMORY_UPDATED).
Governed by Master System Specification Sections 2, 14 & V0.5A Event Bus Spec.
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import uuid


class DistributedEventType(str, Enum):
    NODE_ONLINE = "NODE_ONLINE"
    NODE_OFFLINE = "NODE_OFFLINE"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_PROGRESS = "TASK_PROGRESS"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    MEMORY_UPDATED = "MEMORY_UPDATED"
    PROVIDER_DEGRADED = "PROVIDER_DEGRADED"
    SECURITY_EVENT = "SECURITY_EVENT"


class DistributedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: DistributedEventType
    source_node: str
    target_node: Optional[str] = None  # None = Broadcast
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DistributedEventBus:
    """Publish/subscribe event router coordinating state across local and remote nodes."""

    def __init__(self, node_id: str):
        self.node_id = node_id
        self._subscribers: Dict[DistributedEventType, List[Callable[[DistributedEvent], None]]] = {}
        self.history: List[DistributedEvent] = []

    def subscribe(self, event_type: DistributedEventType, handler: Callable[[DistributedEvent], None]):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def publish(self, event: DistributedEvent):
        self.history.append(event)
        handlers = self._subscribers.get(event.event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                pass

    def get_events(self, event_type: Optional[DistributedEventType] = None) -> List[DistributedEvent]:
        if event_type:
            return [e for e in self.history if e.event_type == event_type]
        return self.history
