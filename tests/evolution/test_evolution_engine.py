"""
HOOD Evolution Engine Comprehensive Test Suite
Governed by Master System Specification Section 2, 8, 14 & V0.5C Directives.

Tests:
1. Model Level Registry & Lineage
2. Experience Collector & Provenance Hashing
3. Secret Redaction & X_SEALED Exclusion from Datasets
4. Project / Tenant Isolation in Datasets
5. Blind Model Arena Execution
6. Capability-Specific Scorecards & Rankings
7. Promotion Lifecycle: SHADOW -> CANARY -> SPECIALIST -> SECONDARY -> CO_PRIMARY -> PRIMARY
8. Anti-Gaming: Rejection of Self-Promotion and Score Modification
9. Human Authorization Gate (Zak) for Consequential Promotion
10. Performance Drift Detection and Automated Demotion
11. Challenger Persistence (Levels 1 and 2 remain challengers when Level 3 promotes)
12. Teacher Disagreement with Deterministic Validation
13. Teacher Dynamic Reliability Calibration
14. Active Learning Selection
15. Hardware-Aware AI Resource Management (Single GPU vs 2x16GB Semantics vs 48GB Node)
16. Cost-Aware Evolution Routing with Privacy & Risk Constraints
17. GPU Break-Even Financial Accounting
18. Emergency Stop Integration with Evolution Subsystem
"""

import pytest
from pathlib import Path
from services.evolution.contracts import (
    ModelIdentity,
    ModelLevel,
    CapabilityDomain,
    PromotionState,
    ArenaTask,
    ArenaAttempt,
    TeacherContribution,
    EvidenceQuality,
    DatasetPrivacyScope,
    HardwareProfile,
    ExecutionRequirement,
    GPUOwnership
)
from services.evolution.model_registry import ModelLevelRegistry
from services.evolution.experience import ExperienceCollector
from services.evolution.arena import ModelArena
from services.evolution.ranking import CapabilityRanker
from services.evolution.promotion import PromotionController, PromotionThresholds
from services.evolution.drift import DriftDetector
from services.evolution.calibration import ActiveLearningSelector, TeacherCalibration
from services.evolution.dataset import DatasetBuilder
from services.evolution.hardware import HardwareResourceManager, GPUBreakEvenCalculator
from services.evolution.routing import CostAwareEvolutionRouter
from services.evolution import EvolutionEngine


@pytest.fixture
def evolution_workspace(tmp_path):
    engine = EvolutionEngine(workspace_root=tmp_path)
    return engine


def test_model_level_registry_and_lineage(evolution_workspace):
    """Verify registration across Levels 1, 2, and 3 with append-only lineage tracking."""
    registry = evolution_workspace.registry

    # Level 1 External
    l1 = registry.get_model("gemini-3.8-flash")
    assert l1 is not None
    assert l1.level == ModelLevel.LEVEL_1_EXTERNAL
    assert l1.promotion_state == PromotionState.PRIMARY

    # Level 2 Self-Hosted
    l2 = registry.get_model("llama3-8b-local")
    assert l2 is not None
    assert l2.level == ModelLevel.LEVEL_2_SELF_HOSTED

    # Level 3 HOOD candidate starts strictly in SHADOW
    l3 = registry.get_model("hood-code-candidate-v1")
    assert l3 is not None
    assert l3.level == ModelLevel.LEVEL_3_HOOD
    assert l3.promotion_state == PromotionState.SHADOW
    assert l3.base_model == "llama3-8b-local"

    # Lineage is preserved
    lineage = registry.get_lineage("hood-code-candidate-v1")
    assert len(lineage) >= 1
    assert lineage[0]["event"] == "REGISTER"
    assert lineage[0]["base_model"] == "llama3-8b-local"


def test_experience_collection_provenance_and_secret_redaction(evolution_workspace):
    """Verify that experience records sanitize secrets and compute SHA256 provenance."""
    collector = evolution_workspace.experience_collector

    rec = collector.record_experience(
        task_type=CapabilityDomain.CODING,
        project_context_id="proj_alpha",
        prompt="Deploy using key sk-live-supersecretapikey123456789 and fix bug",
        deterministic_test_result=True,
        evidence_quality=EvidenceQuality.CONFIRMED_DETERMINISTIC,
        checker_verdict="PASSED",
        actual_task_outcome="SUCCESS",
        selected_final_action="Updated config and deployed safely",
        selection_rationale="Verified with test",
        confidence=0.98,
        privacy_scope=DatasetPrivacyScope.GLOBAL_SANITIZED
    )

    # Secret is redacted
    assert "sk-live-supersecretapikey123456789" not in rec.sanitized_prompt
    assert "[REDACTED_SECRET]" in rec.sanitized_prompt
    # Provenance hash exists
    assert len(rec.provenance_hash) == 64


def test_dataset_privacy_isolation_and_x_sealed_exclusion(evolution_workspace):
    """Verify strict exclusion of X_SEALED, DO_NOT_TRAIN, and project data leakage."""
    collector = evolution_workspace.experience_collector
    builder = evolution_workspace.dataset_builder

    # 1. Normal global record
    collector.record_experience(
        task_type=CapabilityDomain.CODING,
        project_context_id="proj_global",
        prompt="Write a fibonacci sequence generator",
        deterministic_test_result=True,
        checker_verdict="PASSED",
        actual_task_outcome="SUCCESS",
        selected_final_action="def fib(n): ...",
        privacy_scope=DatasetPrivacyScope.GLOBAL_SANITIZED
    )

    # 2. Project isolated record (Client A confidential)
    collector.record_experience(
        task_type=CapabilityDomain.CODING,
        project_context_id="client_a_isolated",
        prompt="Calculate proprietary margin formula for client A",
        deterministic_test_result=True,
        checker_verdict="PASSED",
        actual_task_outcome="SUCCESS",
        selected_final_action="def calc_margin(): ...",
        privacy_scope=DatasetPrivacyScope.PROJECT_ONLY
    )

    # 3. X_SEALED record
    collector.record_experience(
        task_type=CapabilityDomain.CODING,
        project_context_id="X_SEALED_ENGAGEMENT",
        prompt="Adversarial exploit payload metadata",
        deterministic_test_result=True,
        checker_verdict="PASSED",
        actual_task_outcome="SUCCESS",
        selected_final_action="exploit_routine()",
        privacy_scope=DatasetPrivacyScope.X_SEALED
    )

    # Build GLOBAL_SANITIZED dataset
    manifest = builder.build_dataset(
        domain=CapabilityDomain.CODING,
        version="v1.0",
        target_scope=DatasetPrivacyScope.GLOBAL_SANITIZED
    )

    assert manifest.record_count == 1
    assert manifest.x_sealed_strictly_excluded is True

    # Directly verify X_SEALED build request is rejected
    with pytest.raises(PermissionError, match="X_SEALED cannot be targeted"):
        builder.build_dataset(
            domain=CapabilityDomain.CODING,
            version="v1.0_bad",
            target_scope=DatasetPrivacyScope.X_SEALED
        )


def test_blind_model_arena_and_teacher_disagreement(evolution_workspace):
    """Verify blind multi-model arena execution and deterministic ground truth validation."""
    arena = evolution_workspace.arena
    calibration = evolution_workspace.calibration

    task = ArenaTask(
        domain=CapabilityDomain.CODING,
        prompt="What is 2 + 2?",
        deterministic_expected_output="4"
    )

    # Level 1 says "4" (Correct)
    # Level 2 says "5" (Wrong)
    # Level 3 says "4" (Correct)
    invokers = {
        "gemini-2.5-flash": lambda p: {"output": "Result is 4", "level": ModelLevel.LEVEL_1_EXTERNAL, "latency_ms": 120.0, "cost_usd": 0.0001},
        "llama3-8b-local": lambda p: {"output": "Result is 5", "level": ModelLevel.LEVEL_2_SELF_HOSTED, "latency_ms": 45.0, "cost_usd": 0.0},
        "hood-code-candidate-v1": lambda p: {"output": "Result is 4", "level": ModelLevel.LEVEL_3_HOOD, "latency_ms": 50.0, "cost_usd": 0.0}
    }

    trial = arena.run_trial(task, invokers)
    evals = trial["evaluations"]

    assert evals["gemini-2.5-flash"].deterministic_test_passed is True
    assert evals["gemini-2.5-flash"].correctness_score == 1.0

    assert evals["llama3-8b-local"].deterministic_test_passed is False
    assert evals["llama3-8b-local"].correctness_score == 0.0

    assert evals["hood-code-candidate-v1"].deterministic_test_passed is True
    assert evals["hood-code-candidate-v1"].correctness_score == 1.0

    # Calibration updates
    calibration.record_teacher_outcome("gemini-2.5-flash", CapabilityDomain.CODING, was_correct=True)
    calibration.record_teacher_outcome("llama3-8b-local", CapabilityDomain.CODING, was_correct=False)

    assert calibration.get_teacher_reliability("gemini-2.5-flash", CapabilityDomain.CODING) == 1.0
    assert calibration.get_teacher_reliability("llama3-8b-local", CapabilityDomain.CODING) == 0.0


def test_promotion_lifecycle_and_anti_gaming(evolution_workspace):
    """Verify promotion ladder and strict anti-gaming rejection of self-promotion."""
    engine = evolution_workspace
    ranker = engine.ranker
    promo = engine.promotion_controller
    model_id = "hood-code-candidate-v1"
    domain = CapabilityDomain.CODING

    # 1. Anti-gaming test: Model attempting to promote itself is blocked
    self_eval = promo.evaluate_promotion(model_id=model_id, domain=domain, requester=model_id)
    assert self_eval["eligible"] is False
    assert "ANTI_GAMING_VIOLATION" in self_eval["reason"]

    with pytest.raises(PermissionError, match="Self-promotion blocked"):
        promo.execute_promotion(model_id=model_id, domain=domain, target_state=PromotionState.CANARY, authorized_by=model_id)

    # 2. Add sufficient evaluations to qualify for CANARY
    from services.evolution.contracts import EvaluationResult
    for i in range(PromotionThresholds.CANARY_MIN_SAMPLES):
        ranker.record_evaluation(EvaluationResult(
            attempt_id=f"att_{i}",
            model_id=model_id,
            domain=domain,
            correctness_score=1.0,
            evidence_quality=EvidenceQuality.CONFIRMED_DETERMINISTIC,
            deterministic_test_passed=True,
            latency_ms=50.0,
            cost_usd=0.0
        ))

    # Evaluate promotion
    res = promo.evaluate_promotion(model_id=model_id, domain=domain, requester="POLICY_ENGINE")
    assert res["eligible"] is True
    assert res["target_state"] == PromotionState.CANARY.value

    # Execute promotion to CANARY
    decision = promo.execute_promotion(model_id=model_id, domain=domain, target_state=PromotionState.CANARY, authorized_by="POLICY_ENGINE")
    assert decision.to_state == PromotionState.CANARY
    assert engine.registry.get_model(model_id).promotion_state == PromotionState.CANARY

    # 3. Consequential promotion to PRIMARY mandates Zak authorization
    with pytest.raises(PermissionError, match="consequential action requiring explicit authorization from Zak"):
        promo.execute_promotion(model_id=model_id, domain=domain, target_state=PromotionState.PRIMARY, authorized_by="POLICY_ENGINE")

    # Zak authorizes PRIMARY
    zak_decision = promo.execute_promotion(model_id=model_id, domain=domain, target_state=PromotionState.PRIMARY, authorized_by="Zak")
    assert zak_decision.to_state == PromotionState.PRIMARY
    assert zak_decision.is_human_authorized is True
    assert engine.registry.get_model(model_id).promotion_state == PromotionState.PRIMARY


def test_performance_drift_and_automated_demotion(evolution_workspace):
    """Verify performance drift detection triggers demotion recommendation."""
    engine = evolution_workspace
    model_id = "hood-code-candidate-v1"
    domain = CapabilityDomain.CODING

    # Set model to PRIMARY with baseline score 0.95
    engine.registry.get_model(model_id).promotion_state = PromotionState.PRIMARY
    engine.drift_detector.set_baseline(model_id, baseline_score=0.95)

    # Simulate performance drop (accuracy 0.5)
    from services.evolution.contracts import EvaluationResult
    for i in range(10):
        engine.ranker.record_evaluation(EvaluationResult(
            attempt_id=f"drift_att_{i}",
            model_id=model_id,
            domain=domain,
            correctness_score=0.4,
            evidence_quality=EvidenceQuality.DISPROVEN_CONTRADICTED,
            deterministic_test_passed=False,
            latency_ms=2000.0,
            cost_usd=0.0
        ))

    drift_obs = engine.drift_detector.check_drift(model_id, domain)
    assert drift_obs is not None
    assert drift_obs.is_demotion_recommended is True
    assert drift_obs.demotion_target_state == PromotionState.CO_PRIMARY

    # Apply demotion
    demotion_decision = engine.drift_detector.execute_demotion(drift_obs, authorized_by="POLICY_ENGINE")
    assert demotion_decision.to_state == PromotionState.CO_PRIMARY
    assert engine.registry.get_model(model_id).promotion_state == PromotionState.CO_PRIMARY


def test_hardware_resource_placement_and_multi_gpu_semantics():
    """Verify 2x16GB is NEVER treated as a seamless 32GB device."""
    mgr = HardwareResourceManager()

    # Profile A: CPU only
    mgr.register_hardware("cpu_only", HardwareProfile(
        gpu_count=0,
        system_ram_gb=16.0
    ))

    # Profile B: Single 16GB GPU
    mgr.register_hardware("single_16gb", HardwareProfile(
        gpu_count=1,
        per_device_vram_gb=[16.0],
        aggregate_vram_gb=16.0,
        system_ram_gb=32.0,
        cuda_available=True
    ))

    # Profile C: 2 x 16GB GPUs (NOT single 32GB)
    mgr.register_hardware("dual_16gb", HardwareProfile(
        gpu_count=2,
        per_device_vram_gb=[16.0, 16.0],
        aggregate_vram_gb=32.0,
        system_ram_gb=64.0,
        cuda_available=True,
        tensor_parallel_capable=True
    ))

    # Profile D: Single 48GB GPU
    mgr.register_hardware("single_48gb", HardwareProfile(
        gpu_count=1,
        per_device_vram_gb=[48.0],
        aggregate_vram_gb=48.0,
        system_ram_gb=128.0,
        cuda_available=True
    ))

    # Test 1: 24GB single-device workload on dual 16GB MUST FAIL
    req_single_24 = ExecutionRequirement(min_vram_gb=24.0, requires_single_device=True)
    fit_dual = mgr.evaluate_placement("dual_16gb", req_single_24)
    assert fit_dual["fit"] is False
    assert "Single-device requirement not met" in fit_dual["reason"]

    # Test 2: 24GB single-device workload on single 48GB MUST PASS
    fit_48 = mgr.evaluate_placement("single_48gb", req_single_24)
    assert fit_48["fit"] is True
    assert fit_48["device"] == "SINGLE_GPU"

    # Test 3: 24GB distributed workload on dual 16GB (tensor parallel) MUST PASS
    req_dist_24 = ExecutionRequirement(min_vram_gb=24.0, requires_single_device=False)
    fit_dual_dist = mgr.evaluate_placement("dual_16gb", req_dist_24)
    assert fit_dual_dist["fit"] is True
    assert fit_dual_dist["device"] == "MULTI_GPU"
    assert fit_dual_dist["parallel_mode"] == "TENSOR_PARALLEL"


def test_gpu_break_even_financial_accounting():
    """Verify GPU break-even calculator computes realistic financial parity."""
    be = GPUBreakEvenCalculator.calculate_break_even(
        hardware_name="NVIDIA RTX 4090",
        purchase_price_usd=1600.0,
        depreciation_period_months=24,
        average_power_draw_watts=350.0,
        electricity_cost_kwh_usd=0.15,
        monthly_utilization_hours=150.0,
        equivalent_rental_rate_hour_usd=0.79,
        equivalent_api_cost_monthly_usd=180.0
    )

    assert be.monthly_depreciation_usd == 66.67
    assert be.monthly_electricity_cost_usd == 7.88
    assert be.monthly_owned_cost_total_usd == 74.55
    assert be.break_even_vs_rental_months == 14.5
    assert be.break_even_vs_api_months == 9.3
    assert be.purchase_recommended is True


def test_cost_aware_evolution_routing(evolution_workspace):
    """Verify routing constraints: privacy -> self-hosted, high-risk -> Level 1, rented -> approval."""
    router = evolution_workspace.router

    # 1. Privacy mandated -> routes to Level 2 Self-Hosted ($0 API cost)
    r_priv = router.select_execution_route(
        domain=CapabilityDomain.CODING,
        prompt="Analyze proprietary internal code",
        privacy_mandated=True
    )
    assert r_priv["status"] == "ROUTED"
    assert r_priv["level"] in (ModelLevel.LEVEL_2_SELF_HOSTED.value, ModelLevel.LEVEL_3_HOOD.value)
    assert r_priv["estimated_cost_usd"] == 0.0

    # 2. High-risk consequential task (L4) -> routes to Level 1 External
    r_risk = router.select_execution_route(
        domain=CapabilityDomain.CODING,
        prompt="Delete production database table and recreate",
        task_risk="L4",
        privacy_mandated=False
    )
    assert r_risk["status"] == "ROUTED"
    assert r_risk["level"] == ModelLevel.LEVEL_1_EXTERNAL.value

    # 3. Model requiring large VRAM on local dev -> triggers rented GPU path with spending approval
    # Register heavy model
    evolution_workspace.registry.register_model(ModelIdentity(
        model_id="deepseek-r1-70b",
        version="v1",
        provider_runtime="vllm_cluster",
        level=ModelLevel.LEVEL_2_SELF_HOSTED,
        capabilities=[CapabilityDomain.CODING],
        min_execution_req=ExecutionRequirement(min_vram_gb=36.0, requires_single_device=True)
    ))
    r_heavy = router.select_execution_route(
        domain=CapabilityDomain.CODING,
        prompt="Heavy coding distillation",
        task_risk="L1",
        rented_gpu_authorized=False,
        preferred_model_id="deepseek-r1-70b"
    )
    # Rented GPU path requires explicit spend approval
    assert r_heavy["status"] == "APPROVAL_REQUIRED"
    assert r_heavy["spend_approval_required"] is True
    assert "mandates human spend approval" in r_heavy["reason"]


def test_emergency_stop_halts_evolution_workloads(evolution_workspace):
    """Verify EmergencyStopController cleanly handles EvolutionEngine without state corruption."""
    from services.core.emergency_stop import EmergencyStopController
    from services.audit.service import AuditService
    from services.tool_gateway.gateway import ToolGateway

    audit = AuditService(db_path=evolution_workspace.workspace_root / "test_audit.db")
    tg = ToolGateway(workspace_root=evolution_workspace.workspace_root)

    estop = EmergencyStopController(
        tool_gateway=tg,
        audit_service=audit,
        evolution_engine=evolution_workspace
    )

    res = estop.trigger_stop(reason="Emergency Stop during multi-model arena run")
    assert res["status"] == "EMERGENCY_STOP_ACTIVE"
    assert estop.is_active is True
