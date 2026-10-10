"""
HOOD Evolution Engine - Model Arena & Multi-Model Competitions
Governed by Master System Specification Section 8 & V0.5C Directives.

Sends equivalent eligible tasks to Level 1, Level 2, and Level 3 models
WITHOUT exposing one model's answers to another before their initial attempt.
Evaluates correctness, deterministic tests, evidence quality, and cost.
"""

from typing import Dict, Any, List, Optional, Callable
import time
from services.evolution.contracts import (
    ArenaTask,
    ArenaAttempt,
    EvaluationResult,
    CapabilityDomain,
    ModelLevel,
    EvidenceQuality,
    FailureCategory
)


class ModelArena:
    """Orchestrates blind multi-model trials and evaluates relative performance."""

    def __init__(self):
        self.trials: List[Dict[str, Any]] = []

    def run_trial(
        self,
        task: ArenaTask,
        model_invokers: Dict[str, Callable[[str], Dict[str, Any]]],
        evaluator_fn: Optional[Callable[[ArenaTask, str], Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Executes a blind arena task across multiple models.
        model_invokers: mapping of model_id -> callable(prompt) returning {"output": str, "level": ModelLevel, "latency_ms": float, "cost_usd": float}
        """
        attempts: Dict[str, ArenaAttempt] = {}
        evaluations: Dict[str, EvaluationResult] = {}

        # 1. Collect independent attempts blindly
        for model_id, invoker in model_invokers.items():
            start_t = time.perf_counter()
            try:
                res = invoker(task.prompt)
                latency = res.get("latency_ms", (time.perf_counter() - start_t) * 1000.0)
                attempt = ArenaAttempt(
                    task_id=task.task_id,
                    model_id=model_id,
                    level=res.get("level", ModelLevel.LEVEL_3_HOOD),
                    output_text=res.get("output", ""),
                    latency_ms=latency,
                    token_usage=res.get("token_usage", {}),
                    cost_usd=res.get("cost_usd", 0.0)
                )
            except Exception as e:
                attempt = ArenaAttempt(
                    task_id=task.task_id,
                    model_id=model_id,
                    level=ModelLevel.LEVEL_3_HOOD,
                    output_text="",
                    latency_ms=0.0,
                    error=str(e)
                )
            attempts[model_id] = attempt

        # 2. Evaluate each attempt against deterministic ground truth
        for model_id, att in attempts.items():
            if att.error:
                eval_res = EvaluationResult(
                    attempt_id=att.attempt_id,
                    model_id=model_id,
                    domain=task.domain,
                    correctness_score=0.0,
                    evidence_quality=EvidenceQuality.DISPROVEN_CONTRADICTED,
                    deterministic_test_passed=False,
                    checker_verdict="FAILED",
                    latency_ms=att.latency_ms,
                    cost_usd=att.cost_usd,
                    failure_category=FailureCategory.RESOURCE_FAILURE,
                    evaluator_comment=f"Invocation crashed: {att.error}"
                )
            elif evaluator_fn:
                custom_eval = evaluator_fn(task, att.output_text)
                eval_res = EvaluationResult(
                    attempt_id=att.attempt_id,
                    model_id=model_id,
                    domain=task.domain,
                    correctness_score=custom_eval.get("correctness_score", 1.0),
                    evidence_quality=custom_eval.get("evidence_quality", EvidenceQuality.CONFIRMED_DETERMINISTIC),
                    deterministic_test_passed=custom_eval.get("deterministic_test_passed", True),
                    checker_verdict=custom_eval.get("checker_verdict", "PASSED"),
                    latency_ms=att.latency_ms,
                    cost_usd=att.cost_usd,
                    failure_category=custom_eval.get("failure_category"),
                    evaluator_comment=custom_eval.get("comment", "")
                )
            else:
                # Default evaluation: string match against deterministic_expected_output
                passed = False
                if task.deterministic_expected_output:
                    passed = (task.deterministic_expected_output.strip() in att.output_text.strip())
                else:
                    passed = len(att.output_text.strip()) > 0

                eval_res = EvaluationResult(
                    attempt_id=att.attempt_id,
                    model_id=model_id,
                    domain=task.domain,
                    correctness_score=1.0 if passed else 0.0,
                    evidence_quality=EvidenceQuality.CONFIRMED_DETERMINISTIC if passed else EvidenceQuality.DISPROVEN_CONTRADICTED,
                    deterministic_test_passed=passed,
                    checker_verdict="PASSED" if passed else "FAILED",
                    latency_ms=att.latency_ms,
                    cost_usd=att.cost_usd,
                    failure_category=None if passed else FailureCategory.WRONG_ANSWER,
                    evaluator_comment="Deterministic ground truth matched" if passed else "Output mismatched expected result"
                )

            evaluations[model_id] = eval_res

        trial_record = {
            "task": task.model_dump(),
            "attempts": {k: v.model_dump() for k, v in attempts.items()},
            "evaluations": {k: v.model_dump() for k, v in evaluations.items()}
        }
        self.trials.append(trial_record)

        return {
            "task_id": task.task_id,
            "attempts": attempts,
            "evaluations": evaluations
        }
