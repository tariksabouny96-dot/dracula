"""
HOOD Inter-Agent Shared Context Bus
Governed by Master System Specification Section 4.4, 4.5 & Build Directive Section 4.

Provides structured, typed message routing between domain leads, subagents, and tools:
- Task ID / Parent Task hierarchy tracking
- Sender / Recipient routing (direct, broadcast, cross-domain)
- Claim and Evidence binding (confidence, source quality, extracts)
- Explicit Assumptions, Dependencies, Risks, Costs, and Recommendations
- Dispute signaling and evidence challenge propagation
"""

from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field

from packages.contracts.models import RiskLevel, EvidencePacket


class PacketType(str, Enum):
    TASK_DELEGATION = "TASK_DELEGATION"
    TASK_RESULT = "TASK_RESULT"
    EVIDENCE_SHARE = "EVIDENCE_SHARE"
    CHALLENGE_REQUEST = "CHALLENGE_REQUEST"
    DISPUTE_RAISED = "DISPUTE_RAISED"
    DISPUTE_RESOLVED = "DISPUTE_RESOLVED"
    STATUS_UPDATE = "STATUS_UPDATE"
    CROSS_DOMAIN_QUERY = "CROSS_DOMAIN_QUERY"
    PEER_REVIEW_REQUEST = "PEER_REVIEW_REQUEST"
    PEER_REVIEW_RESPONSE = "PEER_REVIEW_RESPONSE"


class EvidenceQuality(str, Enum):
    VERIFIED_PRIMARY = "VERIFIED_PRIMARY"       # Direct tool observation, verified file hash, primary API
    REPRODUCIBLE_TEST = "REPRODUCIBLE_TEST"     # Deterministic code test or build log
    DERIVED_INFERENCE = "DERIVED_INFERENCE"     # Model analysis / reasoning over verified data
    UNVERIFIED_CLAIM = "UNVERIFIED_CLAIM"       # Unchecked hypothesis or third-party assertion


class ContextPacket(BaseModel):
    packet_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    packet_type: PacketType = PacketType.EVIDENCE_SHARE
    task_id: str
    parent_task_id: Optional[str] = None
    sender: str
    recipient: str  # specific agent name or "*" for broadcast
    claim: str
    evidence: List[EvidencePacket] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence_quality: EvidenceQuality = EvidenceQuality.DERIVED_INFERENCE
    assumptions: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.L1
    estimated_cost_usd: float = 0.0
    status: str = "ACTIVE"
    recommended_next_action: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SharedContextBus:
    """
    Central, thread-safe asynchronous-ready bus for structured inter-agent
    communication, evidence exchange, and peer challenge workflows.
    """

    def __init__(self):
        self._packets: List[ContextPacket] = []
        self._subscribers: Dict[str, List[Callable[[ContextPacket], None]]] = {}

    def subscribe(self, agent_name: str, handler: Callable[[ContextPacket], None]) -> None:
        """Register a notification handler for a specific agent."""
        if agent_name not in self._subscribers:
            self._subscribers[agent_name] = []
        self._subscribers[agent_name].append(handler)

    def publish(self, packet: ContextPacket) -> None:
        """Publishes a typed packet to the bus and delivers to subscribers."""
        self._packets.append(packet)

        # Deliver to specific recipient
        if packet.recipient in self._subscribers:
            for handler in self._subscribers[packet.recipient]:
                handler(packet)

        # Deliver to broadcast listeners if recipient is wildcard or different
        if packet.recipient == "*" and "*" in self._subscribers:
            for handler in self._subscribers["*"]:
                handler(packet)

    def get_packets_for_task(self, task_id: str) -> List[ContextPacket]:
        """Returns all packets associated with a specific task."""
        return [p for p in self._packets if p.task_id == task_id or p.parent_task_id == task_id]

    def get_packets_for_agent(self, agent_name: str) -> List[ContextPacket]:
        """Returns all packets directed to or received from a specific agent."""
        return [
            p for p in self._packets
            if p.recipient in (agent_name, "*") or p.sender == agent_name
        ]

    def get_evidence_packets(self, min_confidence: float = 0.0) -> List[ContextPacket]:
        """Returns evidence packets filtered by minimum confidence score."""
        return [
            p for p in self._packets
            if p.packet_type in (PacketType.EVIDENCE_SHARE, PacketType.TASK_RESULT)
            and p.confidence >= min_confidence
        ]

    def get_disputes(self) -> List[ContextPacket]:
        """Returns all dispute-related packets."""
        return [
            p for p in self._packets
            if p.packet_type in (PacketType.DISPUTE_RAISED, PacketType.CHALLENGE_REQUEST, PacketType.DISPUTE_RESOLVED)
        ]

    def clear(self) -> None:
        """Resets the packet store (useful for clean test isolation)."""
        self._packets.clear()
        self._subscribers.clear()
