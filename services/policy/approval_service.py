"""
HOOD Approval Service
Manages approval requests, enforces silence != approval, and verifies checkpoints.
Governed by Master System Specification Section 2.3 & 2.4.
"""

from typing import Dict, Optional, List, Any
from datetime import datetime, timezone
from packages.contracts import ApprovalRequest, ApprovalStatus, RiskLevel
from .governance import RiskEvaluator


class ApprovalService:
    def __init__(self):
        self._requests: Dict[str, ApprovalRequest] = {}

    def create_request(
        self,
        task_id: str,
        action_type: str,
        target: str,
        reason: str,
        risk_level: Optional[RiskLevel] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        recommended_option: str = "",
        checkpoint_ref: Optional[str] = None
    ) -> ApprovalRequest:
        if risk_level is None:
            risk_level = RiskEvaluator.assess_risk(action_type, target)

        req = ApprovalRequest(
            task_id=task_id,
            action_type=action_type,
            target=target,
            risk_level=risk_level,
            reason=reason,
            options=options or [],
            recommended_option=recommended_option,
            checkpoint_ref=checkpoint_ref,
            status=ApprovalStatus.PENDING
        )
        self._requests[req.approval_id] = req
        return req

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        return self._requests.get(approval_id)

    def resolve_request(
        self,
        approval_id: str,
        approved: bool,
        resolved_by: str = "Zak",
        rejection_reason: Optional[str] = None
    ) -> ApprovalRequest:
        req = self._requests.get(approval_id)
        if not req:
            raise KeyError(f"Approval request {approval_id} not found")

        if req.status != ApprovalStatus.PENDING:
            raise ValueError(f"Approval request {approval_id} is already {req.status}")

        req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        req.resolved_at = datetime.now(timezone.utc)
        req.resolved_by = resolved_by
        req.rejection_reason = rejection_reason
        return req

    def is_approved(self, approval_id: str) -> bool:
        """H02: Approval is never inferred from silence."""
        req = self._requests.get(approval_id)
        if not req:
            return False
        return req.status == ApprovalStatus.APPROVED

    def is_rejected(self, approval_id: str) -> bool:
        req = self._requests.get(approval_id)
        if not req:
            return False
        return req.status == ApprovalStatus.REJECTED

    def get_status(self, approval_id: str) -> Optional[ApprovalStatus]:
        req = self._requests.get(approval_id)
        return req.status if req else None

    def list_pending(self) -> List[ApprovalRequest]:
        return [r for r in self._requests.values() if r.status == ApprovalStatus.PENDING]
