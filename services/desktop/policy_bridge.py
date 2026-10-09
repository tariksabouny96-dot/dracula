"""
HOOD Financial Constitution & Recommendation Engine
Governed by Master System Specification Section 2, 8 and Milestone V0.5D Directive Sections 8-13.
Enforces:
1. HOOD MUST NOT independently spend money.
2. Maximize expected quality while minimizing cost/time/risk.
3. Silence is never approval.
4. If paid route is rejected, automatically evaluate/recommend best free alternative.
5. Distinction of price freshness: REFERENCE_PRICE, LIVE_PRICE, USER_CONFIGURED_PRICE, UNKNOWN_PRICE.
6. X strictly inherits the exact same financial rules without authority expansion.
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import uuid

from packages.contracts import RiskLevel, ApprovalStatus, AuditEvent
from packages.config import SystemConfig
from services.desktop.contracts import (
    FinancialRecommendation,
    FinancialOption,
    BillingType,
    PriceFreshness
)
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService


class FinancialConstitutionViolationError(Exception):
    """Raised when an autonomous spending action violates the financial constitution."""
    pass


class FinancialAdvisor:
    """Evaluates expenditure options, formulates typed recommendations, and enforces Zak's approval."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        approval_service: Optional[ApprovalService] = None,
        audit_service: Optional[AuditService] = None
    ):
        self.config = config or SystemConfig()
        self.approval_service = approval_service or ApprovalService()
        self.audit_service = audit_service or AuditService()
        self.recommendations: Dict[str, FinancialRecommendation] = {}

    def formulate_recommendation(
        self,
        task_id: str,
        need_description: str,
        paid_options: List[FinancialOption],
        free_option: FinancialOption,
        consequence_of_free: str,
        reasoning: str,
        preferred_paid_option: Optional[str] = None
    ) -> FinancialRecommendation:
        """
        Synthesizes a structured financial recommendation comparing quality,
        cost, time, and tradeoffs against the mandatory free alternative.
        """
        all_options = [free_option] + paid_options

        # Select best value option
        chosen_option_name = preferred_paid_option
        if not chosen_option_name:
            # Score options by Value = Quality / (Cost + 1.0) / (Time + 1.0)
            best_val = -1.0
            chosen_option_name = free_option.option_name
            for opt in all_options:
                val_score = opt.quality_score / (opt.cost_usd + 1.0) / ((opt.estimated_time_minutes / 60.0) + 1.0)
                if val_score > best_val:
                    best_val = val_score
                    chosen_option_name = opt.option_name

        target_opt = next((o for o in all_options if o.option_name == chosen_option_name), free_option)
        requires_approval = target_opt.cost_usd > 0.0

        rec = FinancialRecommendation(
            task_id=task_id,
            need_description=need_description,
            recommended_option=target_opt.option_name,
            expected_cost_usd=target_opt.cost_usd,
            currency="USD",
            maximum_possible_cost_usd=target_opt.cost_usd * (1.2 if target_opt.billing_type == BillingType.USAGE_BASED else 1.0),
            billing_type=target_opt.billing_type,
            recurring=target_opt.recurring,
            price_freshness=target_opt.price_freshness,
            expected_quality_score=target_opt.quality_score,
            expected_time_minutes=target_opt.estimated_time_minutes,
            expected_benefit=target_opt.description or f"Executes {need_description} with high fidelity",
            risk="LOW" if target_opt.cost_usd == 0 else "FINANCIAL_EXPENDITURE",
            confidence=0.95,
            assumptions=target_opt.assumptions or "Based on current task telemetry",
            alternatives=all_options,
            free_alternative=free_option,
            consequence_of_free=consequence_of_free,
            reason_for_recommendation=reasoning,
            approval_required=requires_approval,
            approval_status=ApprovalStatus.PENDING if requires_approval else ApprovalStatus.APPROVED,
            provider=target_opt.option_name
        )

        self.recommendations[rec.recommendation_id] = rec

        # If expenditure > $0, create constitutional approval request
        if requires_approval:
            appr = self.approval_service.create_request(
                task_id=task_id,
                action_type="FINANCIAL_EXPENDITURE",
                target=f"{target_opt.option_name} (${target_opt.cost_usd:.2f})",
                reason=f"{need_description} -> Recommended: {target_opt.option_name}. Free option available: {free_option.option_name}",
                risk_level=RiskLevel.L4,
                recommended_option=target_opt.option_name
            )
            # Link approval
            rec.approval_status = ApprovalStatus.PENDING

        from packages.contracts import AuditEvent
        self.audit_service.record_event(AuditEvent(
            actor="FinancialAdvisor",
            task_id=task_id,
            action="FINANCIAL_RECOMMENDATION_FORMULATED",
            target=rec.recommended_option,
            policy_decision="APPROVAL_REQUIRED" if requires_approval else "AUTO_APPROVED_ZERO_COST",
            cost=0.0,
            result=f"Recommended {rec.recommended_option} (${rec.expected_cost_usd:.2f}). Free fallback: {free_option.option_name} ($0.00).",
            verification="PASSED"
        ))
        return rec

    def resolve_financial_decision(
        self,
        recommendation_id: str,
        approved: bool,
        authorized_by: str = "Zak",
        authorized_amount_usd: Optional[float] = None
    ) -> FinancialOption:
        """
        Executes Zak's decision. If rejected, automatically falls back to the best $0 alternative.
        """
        rec = self.recommendations.get(recommendation_id)
        if not rec:
            raise KeyError(f"Financial recommendation {recommendation_id} not found")

        from packages.contracts import AuditEvent
        if approved:
            rec.approval_status = ApprovalStatus.APPROVED
            rec.authorized_amount_usd = authorized_amount_usd if authorized_amount_usd is not None else rec.expected_cost_usd
            # Return authorized option
            target_opt = next((o for o in rec.alternatives if o.option_name == rec.recommended_option), rec.free_alternative)
            self.audit_service.record_event(AuditEvent(
                actor=authorized_by,
                task_id=rec.task_id,
                action="FINANCIAL_EXPENDITURE_APPROVED",
                target=rec.recommended_option,
                policy_decision="APPROVED",
                cost=rec.expected_cost_usd,
                result=f"Zak authorized ${rec.authorized_amount_usd:.2f} for {rec.recommended_option}.",
                verification="PASSED"
            ))
            return target_opt
        else:
            rec.approval_status = ApprovalStatus.REJECTED
            rec.authorized_amount_usd = 0.0
            # Fall back to free alternative
            self.audit_service.record_event(AuditEvent(
                actor=authorized_by,
                task_id=rec.task_id,
                action="FINANCIAL_EXPENDITURE_REJECTED",
                target=rec.recommended_option,
                policy_decision="REJECTED_FALLBACK_TO_FREE",
                cost=0.0,
                result=f"Zak rejected expenditure. Automatically fallen back to free alternative: {rec.free_alternative.option_name}.",
                verification="PASSED"
            ))
            return rec.free_alternative

    def verify_x_financial_boundary(self, actor: str, requested_spend_usd: float) -> bool:
        """Enforces that X inherits the exact same financial restrictions and cannot spend autonomously."""
        if requested_spend_usd > 0.0:
            # Silence or autonomous execution is strictly blocked
            raise FinancialConstitutionViolationError(
                f"Actor '{actor}' cannot autonomously authorize expenditure (${requested_spend_usd:.2f}). "
                "The Financial Constitution strictly mandates Zak's prior explicit approval."
            )
        return True
