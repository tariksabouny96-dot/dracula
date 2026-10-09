"""
HOOD Approval Service
Manages approval requests, enforces silence != approval, and verifies checkpoints.
Governed by Master System Specification Section 2.3 & 2.4.
"""

import threading
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone, timedelta
from packages.contracts import ApprovalRequest, ApprovalStatus, RiskLevel
from .governance import RiskEvaluator


DEFAULT_APPROVAL_TTL_SECONDS = 900


class ApprovalService:
    def __init__(self, default_ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS):
        self._requests: Dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()
        self.default_ttl_seconds = default_ttl_seconds

    def _expire(self, req: ApprovalRequest) -> ApprovalRequest:
        """Lazily move stale PENDING/APPROVED requests to EXPIRED (silence != approval)."""
        if req.expires_at and datetime.now(timezone.utc) >= req.expires_at and req.status in (
                ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
            req.status = ApprovalStatus.EXPIRED
        return req

    def create_request(
        self,
        task_id: str,
        action_type: str,
        target: str,
        reason: str,
        risk_level: Optional[RiskLevel] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        recommended_option: str = "",
        checkpoint_ref: Optional[str] = None,
        principal: Optional[str] = None,
        ttl_seconds: Optional[int] = None
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
            status=ApprovalStatus.PENDING,
            principal=principal,
            expires_at=datetime.now(timezone.utc) + timedelta(
                seconds=ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds)
        )
        with self._lock:
            self._requests[req.approval_id] = req
        return req

    def get_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        req = self._requests.get(approval_id)
        return self._expire(req) if req else None

    def resolve_request(
        self,
        approval_id: str,
        approved: bool,
        resolved_by: str = "Zak",
        rejection_reason: Optional[str] = None
    ) -> ApprovalRequest:
        with self._lock:
            req = self._requests.get(approval_id)
            if not req:
                raise KeyError(f"Approval request {approval_id} not found")
            self._expire(req)
            if req.status != ApprovalStatus.PENDING:
                raise ValueError(f"Approval request {approval_id} is already {req.status.value}")

            req.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
            req.resolved_at = datetime.now(timezone.utc)
            req.resolved_by = resolved_by
            req.rejection_reason = rejection_reason
            return req

    def is_approved(self, approval_id: str) -> bool:
        """H02: Approval is never inferred from silence."""
        req = self.get_request(approval_id)
        if not req:
            return False
        return req.status == ApprovalStatus.APPROVED

    def is_rejected(self, approval_id: str) -> bool:
        req = self.get_request(approval_id)
        if not req:
            return False
        return req.status == ApprovalStatus.REJECTED

    def get_status(self, approval_id: str) -> Optional[ApprovalStatus]:
        req = self.get_request(approval_id)
        return req.status if req else None

    def list_pending(self) -> List[ApprovalRequest]:
        return [r for r in list(self._requests.values()) if self._expire(r).status == ApprovalStatus.PENDING]
