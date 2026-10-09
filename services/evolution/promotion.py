"""
HOOD Evolution Engine - Promotion States & Anti-Gaming Controller
Governed by Master System Specification Sections 2, 8 & V0.5C Directives.

CRITICAL RULES:
- Promotion must be earned through evidence across SHADOW -> CANARY -> SPECIALIST -> SECONDARY -> CO_PRIMARY -> PRIMARY.
- NO model may promote itself. Model output is UNTRUSTED input.
- Consequential promotion requires human authorization (Zak).
- Anti-gaming: Model outputs attempting self-promotion, score edits, or checker disabling are rejected.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from services.evolution.contracts import (
    PromotionState,
    PromotionDecision,
    CapabilityDomain,
    ModelIdentity,
    ModelLevel
)
from services.evolution.model_registry import ModelLevelRegistry
from services.evolution.ranking import CapabilityRanker


class PromotionThresholds:
    # Minimum composite score and sample counts required for promotion transitions
    CANARY_MIN_SAMPLES = 5
    CANARY_MIN_SCORE = 0.70

    SPECIALIST_MIN_SAMPLES = 10
    SPECIALIST_MIN_SCORE = 0.85

    SECONDARY_MIN_SAMPLES = 15
    SECONDARY_MIN_SCORE = 0.88

    CO_PRIMARY_MIN_SAMPLES = 20
    CO_PRIMARY_MIN_SCORE = 0.92

    PRIMARY_MIN_SAMPLES = 25
    PRIMARY_MIN_SCORE = 0.95


class PromotionController:
    """Evaluates promotion criteria and enforces anti-gaming and human authority gates."""

    def __init__(self, registry: ModelLevelRegistry, ranker: CapabilityRanker):
        self.registry = registry
        self.ranker = ranker

    def evaluate_promotion(
        self,
        model_id: str,
        domain: CapabilityDomain,
        requester: str = "POLICY_ENGINE"
    ) -> Dict[str, Any]:
        """
        Evaluates whether a model qualifies for promotion based on objective scorecards.
        Enforces:
        - Self-promotion rejection: If requester matches model_id, immediately block.
        """
        # Anti-gaming rule: No model can promote itself
        if requester == model_id:
            return {
                "eligible": False,
                "reason": "ANTI_GAMING_VIOLATION: Models cannot promote themselves or alter their own rank.",
                "target_state": None
            }

        model = self.registry.get_model(model_id)
        if not model:
            return {"eligible": False, "reason": f"Model '{model_id}' not found.", "target_state": None}

        scorecard = self.ranker.get_domain_scorecard(domain, model_id)
        samples = scorecard["sample_count"]
        score = scorecard["composite_score"]
        curr_state = model.promotion_state

        target_state = None

        if curr_state == PromotionState.SHADOW:
            if samples >= PromotionThresholds.CANARY_MIN_SAMPLES and score >= PromotionThresholds.CANARY_MIN_SCORE:
                target_state = PromotionState.CANARY
        elif curr_state == PromotionState.CANARY:
            if samples >= PromotionThresholds.SPECIALIST_MIN_SAMPLES and score >= PromotionThresholds.SPECIALIST_MIN_SCORE:
                target_state = PromotionState.SPECIALIST
        elif curr_state == PromotionState.SPECIALIST:
            if samples >= PromotionThresholds.SECONDARY_MIN_SAMPLES and score >= PromotionThresholds.SECONDARY_MIN_SCORE:
                target_state = PromotionState.SECONDARY
        elif curr_state == PromotionState.SECONDARY:
            if samples >= PromotionThresholds.CO_PRIMARY_MIN_SAMPLES and score >= PromotionThresholds.CO_PRIMARY_MIN_SCORE:
                target_state = PromotionState.CO_PRIMARY
        elif curr_state == PromotionState.CO_PRIMARY:
            if samples >= PromotionThresholds.PRIMARY_MIN_SAMPLES and score >= PromotionThresholds.PRIMARY_MIN_SCORE:
                target_state = PromotionState.PRIMARY

        if not target_state:
            return {
                "eligible": False,
                "current_state": curr_state.value,
                "score": score,
                "samples": samples,
                "reason": "Thresholds not met for next tier."
            }

        # Consequential promotion (to PRIMARY or CO_PRIMARY) requires Zak approval
        requires_human = target_state in (PromotionState.PRIMARY, PromotionState.CO_PRIMARY)

        return {
            "eligible": True,
            "current_state": curr_state.value,
            "target_state": target_state.value,
            "score": score,
            "samples": samples,
            "requires_human_authorization": requires_human
        }

    def execute_promotion(
        self,
        model_id: str,
        domain: CapabilityDomain,
        target_state: PromotionState,
        authorized_by: str = "POLICY_ENGINE"
    ) -> PromotionDecision:
        """Applies promotion state transition if authorization conditions are met."""
        # Anti-gaming rule: No model can authorize its own promotion
        if authorized_by == model_id:
            raise PermissionError("Self-promotion blocked: A model cannot authorize its own promotion.")

        model = self.registry.get_model(model_id)
        if not model:
            raise KeyError(f"Model '{model_id}' not found.")

        # Require Zak's authorization for PRIMARY / CO_PRIMARY
        if target_state in (PromotionState.PRIMARY, PromotionState.CO_PRIMARY):
            if authorized_by != "Zak":
                raise PermissionError(f"Promotion to {target_state.value} is a consequential action requiring explicit authorization from Zak (silence != approval).")

        scorecard = self.ranker.get_domain_scorecard(domain, model_id)
        decision = PromotionDecision(
            model_id=model_id,
            domain=domain,
            from_state=model.promotion_state,
            to_state=target_state,
            benchmark_version="v0.5c-baseline",
            composite_score=scorecard["composite_score"],
            approved_by=authorized_by,
            is_human_authorized=(authorized_by == "Zak"),
            rationale=f"Promotion from {model.promotion_state.value} to {target_state.value} based on composite score {scorecard['composite_score']} across {scorecard['sample_count']} samples."
        )

        self.registry.record_decision(decision)
        return decision
