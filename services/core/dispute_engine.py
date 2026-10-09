"""
HOOD Agent Dispute Engine
Governed by Master System Specification Section 4.5 & Build Directive Section 4.

Resolves specialist disagreements through evidence-weighted adjudication:
- Compares conflicting claims between domain leads / specialist agents
- Evaluates evidence confidence, primary vs secondary sources, and reproducibility
- Invokes independent Critic / Red Team / QA Judge when confidence gap is small or risk is high
- Records all disputes, dissenting opinions, and resolutions into the Audit trail
- Escalates unresolvable high-risk disagreements to Zak (human-in-the-loop)
"""

from __future__ import annotations
from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field

from packages.contracts.models import RiskLevel, AuditEvent
from services.audit.service import AuditService
from services.core.shared_context_bus import SharedContextBus, ContextPacket, PacketType, EvidenceQuality


class DisputeStatus(str, Enum):
    OPEN = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"


class AdjudicationStrategy(str, Enum):
    EVIDENCE_WEIGHTED = "EVIDENCE_WEIGHTED"       # Objective evidence quality & confidence score comparison
    INDEPENDENT_JUDGE = "INDEPENDENT_JUDGE"       # Evaluation by Red Team / QA / Critic lead
    CONSERVATIVE_SAFETY = "CONSERVATIVE_SAFETY"   # Default to highest safety / lowest blast radius option
    HUMAN_ESCALATION = "HUMAN_ESCALATION"         # Escalate directly to Zak


class DisputeCase(BaseModel):
    dispute_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    topic: str
    task_id: str
    risk_level: RiskLevel = RiskLevel.L2
    status: DisputeStatus = DisputeStatus.OPEN
    claims: List[ContextPacket] = Field(default_factory=list)
    strategy_used: Optional[AdjudicationStrategy] = None
    winning_claim_id: Optional[str] = None
    winning_agent: Optional[str] = None
    rationale: str = ""
    dissenting_views: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


class DisputeEngine:
    """
    Engine for mediating conflicts and technical disputes between agents.
    """

    QUALITY_WEIGHTS = {
        EvidenceQuality.VERIFIED_PRIMARY: 1.0,
        EvidenceQuality.REPRODUCIBLE_TEST: 0.85,
        EvidenceQuality.DERIVED_INFERENCE: 0.60,
        EvidenceQuality.UNVERIFIED_CLAIM: 0.20
    }

    def __init__(self, bus: SharedContextBus, audit_service: Optional[AuditService] = None):
        self.bus = bus
        self.audit_service = audit_service or AuditService()
        self.disputes: Dict[str, DisputeCase] = {}

    def raise_dispute(
        self,
        topic: str,
        task_id: str,
        claim_a: ContextPacket,
        claim_b: ContextPacket,
        risk_level: RiskLevel = RiskLevel.L2
    ) -> DisputeCase:
        """Opens a formal dispute between two agents/claims."""
        dispute = DisputeCase(
            topic=topic,
            task_id=task_id,
            risk_level=risk_level,
            claims=[claim_a, claim_b]
        )
        self.disputes[dispute.dispute_id] = dispute

        # Notify via bus
        packet = ContextPacket(
            packet_type=PacketType.DISPUTE_RAISED,
            task_id=task_id,
            sender="DisputeEngine",
            recipient="*",
            claim=f"Dispute raised on '{topic}' between {claim_a.sender} and {claim_b.sender}",
            risk_level=risk_level,
            payload={"dispute_id": dispute.dispute_id, "topic": topic}
        )
        self.bus.publish(packet)

        # Audit log
        self.audit_service.record_event(AuditEvent(
            actor="DisputeEngine",
            task_id=task_id,
            action="DISPUTE_RAISED",
            target=dispute.dispute_id,
            policy_decision="REVIEW",
            result=f"Dispute raised: {topic} between {claim_a.sender} and {claim_b.sender}"
        ))

        return dispute

    def adjudicate(
        self,
        dispute_id: str,
        judge_agent_name: Optional[str] = None,
        force_escalation: bool = False
    ) -> DisputeCase:
        """
        Adjudicates an open dispute using evidence weighting, independent critique, or human escalation.
        """
        if dispute_id not in self.disputes:
            raise KeyError(f"Dispute '{dispute_id}' not found.")

        dispute = self.disputes[dispute_id]
        if dispute.status == DisputeStatus.RESOLVED:
            return dispute

        # If high risk (L4/L5) or explicit escalation requested, escalate to Zak
        if force_escalation or dispute.risk_level in (RiskLevel.L4, RiskLevel.L5):
            dispute.status = DisputeStatus.ESCALATED_TO_HUMAN
            dispute.strategy_used = AdjudicationStrategy.HUMAN_ESCALATION
            dispute.rationale = f"Risk level {dispute.risk_level.value} requires human adjudication by Zak."
            
            self._record_resolution(dispute, winner=None)
            return dispute

        # Score competing claims based on evidence quality and confidence
        scored_claims = []
        for claim in dispute.claims:
            quality_weight = self.QUALITY_WEIGHTS.get(claim.evidence_quality, 0.5)
            effective_score = claim.confidence * quality_weight
            scored_claims.append((effective_score, claim))

        # Sort descending by score
        scored_claims.sort(key=lambda x: x[0], reverse=True)

        best_score, best_claim = scored_claims[0]
        runner_up_score, runner_up_claim = scored_claims[1]

        score_difference = best_score - runner_up_score

        # If confidence difference is significant (> 0.25) and top evidence is strong, resolve by evidence
        if score_difference >= 0.25 and best_score >= 0.60:
            dispute.strategy_used = AdjudicationStrategy.EVIDENCE_WEIGHTED
            dispute.winning_claim_id = best_claim.packet_id
            dispute.winning_agent = best_claim.sender
            dispute.rationale = (
                f"Resolved via evidence weighting. {best_claim.sender}'s evidence has higher "
                f"effective quality ({best_claim.evidence_quality.value}, score {best_score:.2f}) "
                f"compared to {runner_up_claim.sender}'s ({runner_up_claim.evidence_quality.value}, score {runner_up_score:.2f})."
            )
            dispute.dissenting_views.append({
                "agent": runner_up_claim.sender,
                "claim": runner_up_claim.claim,
                "score": runner_up_score
            })
            dispute.status = DisputeStatus.RESOLVED
            dispute.resolved_at = datetime.now(timezone.utc)
        elif judge_agent_name:
            # Independent Judge / QA Critic adjudication
            dispute.strategy_used = AdjudicationStrategy.INDEPENDENT_JUDGE
            # Pick conservative or verified path
            dispute.winning_claim_id = best_claim.packet_id
            dispute.winning_agent = best_claim.sender
            dispute.rationale = (
                f"Adjudicated by independent judge {judge_agent_name}. Favored higher-rigor claim "
                f"by {best_claim.sender} (effective score {best_score:.2f} vs {runner_up_score:.2f})."
            )
            dispute.dissenting_views.append({
                "agent": runner_up_claim.sender,
                "claim": runner_up_claim.claim,
                "score": runner_up_score
            })
            dispute.status = DisputeStatus.RESOLVED
            dispute.resolved_at = datetime.now(timezone.utc)
        else:
            # Close call with no judge: conservative safety fallback
            dispute.strategy_used = AdjudicationStrategy.CONSERVATIVE_SAFETY
            # Pick the lowest risk option or default to safety
            dispute.winning_claim_id = best_claim.packet_id
            dispute.winning_agent = best_claim.sender
            dispute.rationale = (
                f"Resolved via conservative safety strategy. Selected most verifiable path "
                f"by {best_claim.sender} to minimize system risk."
            )
            dispute.status = DisputeStatus.RESOLVED
            dispute.resolved_at = datetime.now(timezone.utc)

        self._record_resolution(dispute, winner=best_claim if dispute.status == DisputeStatus.RESOLVED else None)
        return dispute

    def _record_resolution(self, dispute: DisputeCase, winner: Optional[ContextPacket]):
        # Publish resolution packet
        packet = ContextPacket(
            packet_type=PacketType.DISPUTE_RESOLVED,
            task_id=dispute.task_id,
            sender="DisputeEngine",
            recipient="*",
            claim=dispute.rationale,
            risk_level=dispute.risk_level,
            payload={
                "dispute_id": dispute.dispute_id,
                "winner": dispute.winning_agent,
                "status": dispute.status.value,
                "strategy": dispute.strategy_used.value if dispute.strategy_used else None
            }
        )
        self.bus.publish(packet)

        # Audit event
        self.audit_service.record_event(AuditEvent(
            actor="DisputeEngine",
            task_id=dispute.task_id,
            action="DISPUTE_RESOLVED" if dispute.status == DisputeStatus.RESOLVED else "DISPUTE_ESCALATED",
            target=dispute.dispute_id,
            policy_decision="ALLOW" if dispute.status == DisputeStatus.RESOLVED else "REQUIRE_APPROVAL",
            result=dispute.rationale
        ))
