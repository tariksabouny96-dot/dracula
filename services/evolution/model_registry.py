"""
HOOD Evolution Engine - Model Level Registry & Lineage Tracker
Classifies models across Level 1 (External), Level 2 (Self-Hosted), and Level 3 (HOOD-Owned).
Maintains append-only version lineage, benchmark attachments, and promotion states.
"""

from typing import Dict, Any, List, Optional
from services.evolution.contracts import (
    ModelIdentity,
    ModelLevel,
    CapabilityDomain,
    PromotionState,
    PromotionDecision
)


class ModelLevelRegistry:
    """Manages multi-level model identities, immutable lineage, and capability associations."""

    def __init__(self):
        self._models: Dict[str, ModelIdentity] = {}
        self._lineage_history: List[Dict[str, Any]] = []
        self._promotion_decisions: List[PromotionDecision] = []

    def register_model(self, model: ModelIdentity):
        # Level 3 must default to SHADOW unless explicitly initialized
        if model.level == ModelLevel.LEVEL_3_HOOD and not model.promotion_state:
            model.promotion_state = PromotionState.SHADOW
        self._models[model.model_id] = model
        self._lineage_history.append({
            "event": "REGISTER",
            "model_id": model.model_id,
            "level": model.level.value,
            "version": model.version,
            "base_model": model.base_model,
            "dataset_version": model.dataset_version,
            "training_recipe": model.training_recipe
        })

    def get_model(self, model_id: str) -> Optional[ModelIdentity]:
        return self._models.get(model_id)

    def list_models_by_level(self, level: ModelLevel) -> List[ModelIdentity]:
        return [m for m in self._models.values() if m.level == level]

    def list_models_for_capability(self, domain: CapabilityDomain) -> List[ModelIdentity]:
        return [m for m in self._models.values() if domain in m.capabilities and m.available]

    def record_decision(self, decision: PromotionDecision):
        """Records an approved promotion or demotion decision."""
        model = self.get_model(decision.model_id)
        if not model:
            raise KeyError(f"Model '{decision.model_id}' not found.")

        # Update model state
        model.promotion_state = decision.to_state
        self._promotion_decisions.append(decision)
        self._lineage_history.append({
            "event": "STATE_CHANGE",
            "model_id": decision.model_id,
            "from_state": decision.from_state.value,
            "to_state": decision.to_state.value,
            "domain": decision.domain.value,
            "approved_by": decision.approved_by,
            "rationale": decision.rationale
        })

    def get_lineage(self, model_id: str) -> List[Dict[str, Any]]:
        return [h for h in self._lineage_history if h.get("model_id") == model_id]
