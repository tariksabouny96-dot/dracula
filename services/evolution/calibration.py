"""
HOOD Evolution Engine - Active Learning Selector & Teacher Calibration
Governed by Master System Specification Section 8 & V0.5C Directives.

CRITICAL RULES:
- Selective evaluation: Not every task needs expensive multi-model parallel inference.
- Teacher trust is dynamic: Tracks historical teacher performance per domain (e.g. Teacher A coding reliability 0.96 vs 0.89).
"""

from typing import Dict, Any, List, Optional
from services.evolution.contracts import (
    CapabilityDomain,
    ModelLevel,
    TeacherContribution
)


class ActiveLearningSelector:
    """Selects high-value tasks for teacher/challenger parallel comparison while conserving API budget."""

    @staticmethod
    def should_trigger_arena(
        domain: CapabilityDomain,
        level_3_confidence: float,
        is_novel_task: bool = False,
        is_high_risk: bool = False
    ) -> Dict[str, Any]:
        """
        Determines whether to trigger parallel multi-model arena comparison.
        Triggers on:
        - Low Level 3 confidence (< 0.75)
        - High-risk task (L3+)
        - Novel task distribution
        """
        reasons = []
        if level_3_confidence < 0.75:
            reasons.append(f"Level 3 low confidence ({level_3_confidence} < 0.75)")
        if is_high_risk:
            reasons.append("High risk consequential task requires multi-model verification")
        if is_novel_task:
            reasons.append("Novel task distribution provides high learning value")

        should_run = len(reasons) > 0
        return {
            "trigger_arena": should_run,
            "reasons": reasons,
            "cost_saving_active": not should_run
        }


class TeacherCalibration:
    """Tracks dynamic teacher reliability per domain based on ground truth validation."""

    def __init__(self):
        # teacher_id -> domain -> {"samples": int, "correct": int, "reliability": float}
        self._calibration: Dict[str, Dict[CapabilityDomain, Dict[str, Any]]] = {}

    def record_teacher_outcome(self, teacher_id: str, domain: CapabilityDomain, was_correct: bool):
        if teacher_id not in self._calibration:
            self._calibration[teacher_id] = {}
        if domain not in self._calibration[teacher_id]:
            self._calibration[teacher_id][domain] = {"samples": 0, "correct": 0, "reliability": 0.5}

        entry = self._calibration[teacher_id][domain]
        entry["samples"] += 1
        if was_correct:
            entry["correct"] += 1
        entry["reliability"] = round(entry["correct"] / entry["samples"], 3)

    def get_teacher_reliability(self, teacher_id: str, domain: CapabilityDomain) -> float:
        entry = self._calibration.get(teacher_id, {}).get(domain)
        if not entry or entry["samples"] == 0:
            return 0.5  # Neutral default prior
        return entry["reliability"]

    def weight_contributions(self, contributions: List[TeacherContribution], domain: CapabilityDomain) -> Dict[str, float]:
        """Calculates dynamic weighting for teacher contributions based on calibrated reliability."""
        weights = {}
        for c in contributions:
            rel = self.get_teacher_reliability(c.teacher_model_id, domain)
            c.historical_domain_reliability = rel
            weights[c.teacher_model_id] = rel
        return weights
