"""
HOOD Evolution Engine - Cost-Aware Intelligent Model Router
Governed by Master System Specification Sections 2, 8 & V0.5C Directives.

Routes tasks to optimize total cost while meeting required Quality, Privacy, Latency, and Security constraints.
Avoids simplistic "always cheapest" or "always strongest".
Supports rented GPU estimate paths with approval gating.
"""

from typing import Dict, Any, List, Optional
from services.evolution.contracts import (
    CapabilityDomain,
    ModelLevel,
    ModelIdentity,
    ExecutionRequirement,
    GPUOwnership
)
from services.evolution.model_registry import ModelLevelRegistry
from services.evolution.hardware import HardwareResourceManager


class CostAwareEvolutionRouter:
    """Selects the optimal model and hardware execution path under constrained optimization."""

    def __init__(self, registry: ModelLevelRegistry, hardware_mgr: HardwareResourceManager):
        self.registry = registry
        self.hardware_mgr = hardware_mgr

    def select_execution_route(
        self,
        domain: CapabilityDomain,
        prompt: str,
        task_risk: str = "L1",
        privacy_mandated: bool = False,
        budget_limit_usd: float = 1.0,
        rented_gpu_authorized: bool = False,
        preferred_model_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Determines optimal execution path:
        1. Explicit preferred model if specified.
        2. Private routine tasks -> Level 2 Local / Self-Hosted.
        3. High-risk consequential tasks -> Strong Level 1 External + Checker.
        4. Simple classification / coding -> Qualified Level 2 or Level 3 Shadow/Canary.
        5. Rented GPU path requires explicit human spend authorization.
        """
        candidate_models = self.registry.list_models_for_capability(domain)
        if preferred_model_id:
            pref = self.registry.get_model(preferred_model_id)
            if pref:
                candidate_models = [pref]
        if not candidate_models:
            return {"status": "NO_MODEL_AVAILABLE", "reason": f"No models registered for domain {domain.value}"}

        selected_model: Optional[ModelIdentity] = None
        selected_hardware = "local_dev"
        spend_approval_needed = False
        decision_reason = ""

        # Case 1: Privacy mandated -> MUST be Level 2 Self-Hosted or Level 3 HOOD
        if privacy_mandated:
            local_candidates = [m for m in candidate_models if m.level in (ModelLevel.LEVEL_2_SELF_HOSTED, ModelLevel.LEVEL_3_HOOD)]
            if not local_candidates:
                return {
                    "status": "BLOCKED_PRIVACY_CONSTRAINT",
                    "reason": "Workload mandates local privacy but no Level 2 or Level 3 local model is available."
                }
            selected_model = local_candidates[0]
            decision_reason = "Mandatory privacy policy: routed to self-hosted local model."

        # Case 2: High-risk consequential task (L3+) -> Strong Level 1 External + Independent Validator
        elif task_risk in ("L3", "L4", "L5"):
            ext_candidates = [m for m in candidate_models if m.level == ModelLevel.LEVEL_1_EXTERNAL]
            if ext_candidates:
                selected_model = ext_candidates[0]
                decision_reason = "High-risk consequential task: routed to frontier Level 1 model with Maker-Checker attached."
            else:
                selected_model = candidate_models[0]
                decision_reason = "High-risk task: Level 1 unavailable, routing to primary available candidate."

        # Case 3: Standard routine task -> evaluate cost-efficiency
        else:
            # Prefer Level 3 if Co-Primary/Primary, else Level 2 local if cheap, else Level 1
            l3_primaries = [m for m in candidate_models if m.level == ModelLevel.LEVEL_3_HOOD and m.promotion_state.value in ("PRIMARY", "CO_PRIMARY")]
            l2_local = [m for m in candidate_models if m.level == ModelLevel.LEVEL_2_SELF_HOSTED]
            l1_ext = [m for m in candidate_models if m.level == ModelLevel.LEVEL_1_EXTERNAL]

            if l3_primaries:
                selected_model = l3_primaries[0]
                decision_reason = "Routed to validated Level 3 HOOD model."
            elif l2_local:
                selected_model = l2_local[0]
                decision_reason = "Routine task: routed to low-marginal-cost Level 2 local model ($0 API cost)."
            elif l1_ext:
                selected_model = l1_ext[0]
                decision_reason = "Routine task: fallback to external Level 1 provider."
            else:
                selected_model = candidate_models[0]
                decision_reason = "Default route to available model."

        # Check hardware placement if model has VRAM requirement
        hw_eval = self.hardware_mgr.evaluate_placement(selected_hardware, selected_model.min_execution_req)

        # Check if rented GPU is needed
        if not hw_eval["fit"]:
            # Check rented GPU profile
            rented_eval = self.hardware_mgr.evaluate_placement("rented_gpu_cluster", selected_model.min_execution_req)
            if rented_eval["fit"]:
                if not rented_gpu_authorized:
                    spend_approval_needed = True
                    return {
                        "status": "APPROVAL_REQUIRED",
                        "model_id": selected_model.model_id,
                        "hardware": "rented_gpu_cluster",
                        "spend_approval_required": True,
                        "reason": f"Local hardware insufficient ({hw_eval['reason']}). Rented GPU satisfies requirement but mandates human spend approval."
                    }
                else:
                    selected_hardware = "rented_gpu_cluster"

        # Calculate estimated cost
        prompt_tokens = len(prompt.split())
        est_cost = (prompt_tokens / 1000.0) * selected_model.cost_per_1k_input

        return {
            "status": "ROUTED",
            "model_id": selected_model.model_id,
            "level": selected_model.level.value,
            "hardware": selected_hardware,
            "estimated_cost_usd": round(est_cost, 6),
            "spend_approval_required": spend_approval_needed,
            "reason": decision_reason
        }
