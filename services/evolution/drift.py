"""
HOOD Evolution Engine - Demotion & Drift Detection
Detects model performance regressions and executes automated demotions.
Ensures Level 1 and Level 2 models remain active challengers even when Level 3 is primary.
"""

from typing import Dict, Any, List, Optional
from services.evolution.contracts import (
    DriftObservation,
    PromotionState,
    PromotionDecision,
    CapabilityDomain
)
from services.evolution.model_registry import ModelLevelRegistry
from services.evolution.ranking import CapabilityRanker


class DriftDetector:
    """Monitors performance changes against baseline and triggers demotion recommendations."""

    def __init__(self, registry: ModelLevelRegistry, ranker: CapabilityRanker, drift_threshold: float = 0.15):
        self.registry = registry
        self.ranker = ranker
        self.drift_threshold = drift_threshold  # 15% drop triggers demotion recommendation
        self.baselines: Dict[str, float] = {}

    def set_baseline(self, model_id: str, baseline_score: float):
        self.baselines[model_id] = baseline_score

    def check_drift(self, model_id: str, domain: CapabilityDomain) -> Optional[DriftObservation]:
        model = self.registry.get_model(model_id)
        if not model or model.model_id not in self.baselines:
            return None

        baseline = self.baselines[model_id]
        scorecard = self.ranker.get_domain_scorecard(domain, model_id)
        current = scorecard["composite_score"]

        if baseline <= 0:
            return None

        drop = (baseline - current) / baseline

        is_demotion_rec = (drop >= self.drift_threshold)
        target_state = None

        if is_demotion_rec:
            # Map demotion ladder
            demotion_map = {
                PromotionState.PRIMARY: PromotionState.CO_PRIMARY,
                PromotionState.CO_PRIMARY: PromotionState.SECONDARY,
                PromotionState.SECONDARY: PromotionState.CANARY,
                PromotionState.SPECIALIST: PromotionState.CANARY,
                PromotionState.CANARY: PromotionState.SHADOW,
                PromotionState.SHADOW: PromotionState.SUSPENDED
            }
            target_state = demotion_map.get(model.promotion_state, PromotionState.SUSPENDED)

        return DriftObservation(
            model_id=model_id,
            domain=domain,
            baseline_score=baseline,
            current_score=current,
            drop_percentage=round(drop, 3),
            is_demotion_recommended=is_demotion_rec,
            demotion_target_state=target_state
        )

    def execute_demotion(self, drift_obs: DriftObservation, authorized_by: str = "POLICY_ENGINE") -> PromotionDecision:
        """Applies demotion state transition."""
        model = self.registry.get_model(drift_obs.model_id)
        if not model or not drift_obs.demotion_target_state:
            raise ValueError("Invalid demotion observation.")

        decision = PromotionDecision(
            model_id=drift_obs.model_id,
            domain=drift_obs.domain,
            from_state=model.promotion_state,
            to_state=drift_obs.demotion_target_state,
            benchmark_version="v0.5c-baseline",
            composite_score=drift_obs.current_score,
            approved_by=authorized_by,
            is_human_authorized=(authorized_by == "Zak"),
            rationale=f"Automated demotion triggered by performance drift: {drift_obs.drop_percentage * 100}% score drop from baseline {drift_obs.baseline_score} to {drift_obs.current_score}."
        )

        self.registry.record_decision(decision)
        return decision
