"""
HOOD Evolution Engine - Capability-Specific Ranking & Scorecards
Ranks models per capability domain (Coding, Commerce, Security, etc.)
balancing Quality, Cost, Latency, and Policy Compliance.
"""

from typing import Dict, Any, List, Optional
from services.evolution.contracts import (
    CapabilityDomain,
    EvaluationResult,
    ModelLevel
)


class CapabilityRanker:
    """Computes multidimensional domain scorecards and ranks models per capability."""

    def __init__(self):
        # domain -> model_id -> list of EvaluationResults
        self._domain_history: Dict[CapabilityDomain, Dict[str, List[EvaluationResult]]] = {}

    def record_evaluation(self, eval_res: EvaluationResult):
        domain = eval_res.domain
        if domain not in self._domain_history:
            self._domain_history[domain] = {}
        if eval_res.model_id not in self._domain_history[domain]:
            self._domain_history[domain][eval_res.model_id] = []
        self._domain_history[domain][eval_res.model_id].append(eval_res)

    def get_domain_scorecard(self, domain: CapabilityDomain, model_id: str) -> Dict[str, Any]:
        records = self._domain_history.get(domain, {}).get(model_id, [])
        if not records:
            return {
                "model_id": model_id,
                "domain": domain.value,
                "sample_count": 0,
                "accuracy": 0.0,
                "avg_latency_ms": 0.0,
                "avg_cost_usd": 0.0,
                "composite_score": 0.0
            }

        sample_count = len(records)
        accuracy = sum(r.correctness_score for r in records) / sample_count
        avg_latency = sum(r.latency_ms for r in records) / sample_count
        avg_cost = sum(r.cost_usd for r in records) / sample_count
        test_pass_rate = sum(1 for r in records if r.deterministic_test_passed) / sample_count

        # Composite score: 60% accuracy, 20% test pass rate, 10% latency factor, 10% cost factor
        latency_penalty = min(0.1, (avg_latency / 10000.0) * 0.1)
        cost_penalty = min(0.1, (avg_cost / 1.0) * 0.1)
        composite = max(0.0, (0.6 * accuracy) + (0.2 * test_pass_rate) + (0.1 - latency_penalty) + (0.1 - cost_penalty))

        return {
            "model_id": model_id,
            "domain": domain.value,
            "sample_count": sample_count,
            "accuracy": round(accuracy, 3),
            "test_pass_rate": round(test_pass_rate, 3),
            "avg_latency_ms": round(avg_latency, 1),
            "avg_cost_usd": round(avg_cost, 4),
            "composite_score": round(composite, 3)
        }

    def rank_models_for_domain(self, domain: CapabilityDomain) -> List[Dict[str, Any]]:
        models = list(self._domain_history.get(domain, {}).keys())
        scorecards = [self.get_domain_scorecard(domain, m) for m in models]
        # Sort by composite_score descending
        return sorted(scorecards, key=lambda x: x["composite_score"], reverse=True)
