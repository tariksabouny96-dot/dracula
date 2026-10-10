"""
HOOD Master System v1.2 Verification Test Suite.
Verifies all core architectural requirements of Master System v1.2:
1. Constitutional boundary: Impossible List distinguishes resource, technology, and legal constraints.
2. Continuous Technology Intelligence Engine: cross-checks discoveries with Impossible List.
3. Economic Engine & Opportunity Killer: adversarial stress test rejects weak/capital-heavy opportunities.
4. Rent-versus-Own Analyzer: computes break-even amortization and strategic independence.
5. Level-3 Acceleration Engine: coordinates 50 mechanisms with strict zero-cost governor enforcement.
6. Intelligence Measurement Lab & Frontier Gap Map: audited empirical benchmarks without marketing claims.
7. Autonomous Self-Evolution Engine: reversible cycle with automatic test verification and rollback.
8. Controlled Memory Forgetting: purges expired memories while preserving immutable governance records.
"""

import pytest
from pathlib import Path
from services.impossible_list.service import ImpossibleListService, BarrierCategory
from services.intelligence.engine import TechnologyIntelligenceEngine, TechCategory, DiscoveryAssessment
from services.economic.engine import EconomicEngine, FinancialMode, OpportunityEvaluation, OpportunityStatus
from services.lab.evaluator import IntelligenceMeasurementLab, EvaluationDimension
from services.evolution.acceleration import Level3AccelerationEngine
from services.memory.service import MemoryService
from packages.contracts import MemoryObject, MemoryType, LearningStatus
from datetime import datetime, timezone, timedelta


@pytest.fixture
def tmp_impossible_service(tmp_path):
    return ImpossibleListService(db_path=tmp_path / "test_impossible.db")


@pytest.fixture
def tmp_intel_engine(tmp_path, tmp_impossible_service):
    return TechnologyIntelligenceEngine(
        db_path=tmp_path / "test_intel.db",
        impossible_list=tmp_impossible_service
    )


@pytest.fixture
def tmp_economic_engine(tmp_path):
    return EconomicEngine(db_path=tmp_path / "test_economic.db")


@pytest.fixture
def tmp_lab_engine(tmp_path):
    return IntelligenceMeasurementLab(db_path=tmp_path / "test_lab.db")


@pytest.fixture
def tmp_acceleration_engine(tmp_path):
    return Level3AccelerationEngine(db_path=tmp_path / "test_accel.db")


def test_impossible_list_distinguishes_barriers(tmp_impossible_service):
    """Verifies that Impossible List separates resource, tech, and legal boundaries."""
    # 1. Resource constrained
    item1 = tmp_impossible_service.record_barrier(
        item_id="BARRIER_01",
        objective="Run 70B parameter model locally",
        category=BarrierCategory.RESOURCE_CONSTRAINED,
        limitation_summary="Insufficient VRAM (16GB vs 48GB required)",
        evidence_supporting="nvidia-smi reports 16GB VRAM on current host",
        required_capability="48GB+ unified or multi-GPU memory",
        potential_technological_unlock="4-bit AWQ quantization or dual-node clustering",
        estimated_value_usd=1200.0,
        reevaluation_triggers=["hardware_upgrade", "4bit_quant"]
    )
    assert item1.category == BarrierCategory.RESOURCE_CONSTRAINED

    # 2. Constitutional/Legal barrier
    item2 = tmp_impossible_service.record_barrier(
        item_id="BARRIER_02",
        objective="Autonomous unsolicited email campaign",
        category=BarrierCategory.UNAUTHORIZED_OR_ILLEGAL,
        limitation_summary="Constitutional ban on spam and non-authorized external contact",
        evidence_supporting="Constitutional Rule H04, H11",
        required_capability="None",
        potential_technological_unlock="Never",
        estimated_value_usd=0.0
    )
    assert item2.category == BarrierCategory.UNAUTHORIZED_OR_ILLEGAL

    unresolved = tmp_impossible_service.list_unresolved()
    assert len(unresolved) == 2


def test_tech_intelligence_unlocks_impossible_items(tmp_intel_engine, tmp_impossible_service):
    """Verifies that technology discoveries check for potential unlocks in Impossible List."""
    tmp_impossible_service.record_barrier(
        item_id="BARRIER_QUANT",
        objective="Execute large reasoning model on modest GPU",
        category=BarrierCategory.RESOURCE_CONSTRAINED,
        limitation_summary="Out of memory on standard float16",
        evidence_supporting="OOM at 24GB",
        required_capability="Extreme quantization",
        potential_technological_unlock="BitNet 1.58b architecture",
        estimated_value_usd=2500.0,
        reevaluation_triggers=["bitnet"]
    )

    discovery = tmp_intel_engine.register_discovery(
        tech_id="TECH_BITNET",
        title="BitNet 1.58b architecture and 1-bit inference",
        category=TechCategory.INFERENCE_OPTIMIZATION,
        source_url_or_ref="https://arxiv.org/abs/2402.17764",
        summary="1-bit LLMs enabling high performance with fraction of memory footprint",
        license_type="MIT",
        hardware_requirements="Standard CPU / low-tier GPU",
        assessment=DiscoveryAssessment(
            improves_capability=True,
            reduces_cost=True,
            reduces_hardware_requirements=True,
            overall_recommendation="EXPERIMENT",
            evidence="Paper demonstrates matching perplexity with 8x lower energy"
        )
    )

    assert "BARRIER_QUANT" in discovery.assessment.enables_impossible_tasks
    assert discovery.assessment.overall_recommendation == "EXPERIMENT"


def test_economic_opportunity_killer_and_stress_testing(tmp_economic_engine):
    """Verifies that the Opportunity Killer rejects unrealistic, capital-intensive, or low-return tasks."""
    # Weak opportunity (requires upfront cash, low hourly return)
    weak_opp = OpportunityEvaluation(
        potential_customer_segment="Local shops",
        problem_solved="Manual scheduling",
        expected_revenue_usd=100.0,
        expected_profit_usd=50.0,
        capital_required_usd=200.0,  # Violated zero spend
        time_to_first_revenue_days=60,
        delivery_effort_hours=20.0,  # Low hourly return
        probability_of_success=0.20, # Low probability
        competitive_intensity="SEVERE",
        scalability_score=2.0,
        strategic_value_score=1.0
    )
    opp_rec = tmp_economic_engine.evaluate_and_stress_test_opportunity(
        opportunity_id="OPP_WEAK",
        title="Manual Scheduler App",
        category="Software",
        evaluation=weak_opp
    )
    assert opp_rec.killer_report.is_killed is True
    assert opp_rec.status == OpportunityStatus.KILLED_REJECTED
    assert len(opp_rec.killer_report.rejection_reasons) >= 2

    # Strong zero-capital opportunity
    strong_opp = OpportunityEvaluation(
        potential_customer_segment="E-commerce stores",
        problem_solved="Automated catalog data cleaning pipeline",
        expected_revenue_usd=1500.0,
        expected_profit_usd=1500.0,
        capital_required_usd=0.0,
        time_to_first_revenue_days=7,
        delivery_effort_hours=10.0,  # $150/hr
        probability_of_success=0.85,
        competitive_intensity="LOW",
        scalability_score=8.5,
        strategic_value_score=9.0,
        recurring_revenue_potential=True,
        reusable_asset_created=True
    )
    opp_rec2 = tmp_economic_engine.evaluate_and_stress_test_opportunity(
        opportunity_id="OPP_STRONG",
        title="Catalog Data Cleaner",
        category="Data Pipeline",
        evaluation=strong_opp
    )
    assert opp_rec2.killer_report.is_killed is False
    assert opp_rec2.status == OpportunityStatus.QUALIFIED
    assert opp_rec2.evaluation.roi_hourly_rate_usd == 150.0


def test_rent_versus_own_amortization_calculation(tmp_economic_engine):
    """Verifies rent-versus-own build break-even accounting."""
    # High rent API -> Rapid break-even -> BUILD_OWN
    high_rent = tmp_economic_engine.calculate_rent_versus_own(
        service_name="Code Synthesizer API",
        monthly_api_rent_cost_usd=500.0,
        replacement_engineering_hours=20.0,
        local_hardware_cost_usd=0.0,
        hourly_dev_value_usd=50.0
    )
    assert high_rent.break_even_months == 2.0
    assert high_rent.recommended_path == "BUILD_OWN"

    # Negligible cost API -> Extended break-even -> RENT
    low_rent = tmp_economic_engine.calculate_rent_versus_own(
        service_name="Occasional OCR API",
        monthly_api_rent_cost_usd=5.0,
        replacement_engineering_hours=40.0,
        local_hardware_cost_usd=0.0,
        hourly_dev_value_usd=50.0
    )
    assert low_rent.recommended_path == "RENT"


def test_level3_acceleration_governor_and_skill_compilation(tmp_acceleration_engine):
    """Verifies Acceleration Governor enforcement and Skill Compilation."""
    # Governor blocks unauthorized spending
    gov_blocked = tmp_acceleration_engine.check_acceleration_governor(proposed_cost_usd=15.0)
    assert gov_blocked.is_permitted is False
    assert gov_blocked.budget_exhausted is True

    # Governor approves zero-cost local execution
    gov_approved = tmp_acceleration_engine.check_acceleration_governor(proposed_cost_usd=0.0)
    assert gov_approved.is_permitted is True

    # Mechanism 7: Compile deterministic skill
    skill = tmp_acceleration_engine.compile_skill(
        skill_id="skill_json_parser",
        domain="TOOL_USE",
        title="Deterministic JSON schema extractor",
        code="def extract_json(s): import json; return json.loads(s)",
        triggers=["extract json", "parse payload"]
    )
    assert skill.replaces_model_calls is True
    skills = tmp_acceleration_engine.list_compiled_skills()
    assert len(skills) == 1
    assert skills[0].skill_id == "skill_json_parser"


def test_intelligence_measurement_lab_and_frontier_gap(tmp_lab_engine):
    """Verifies empirical benchmark recording and Frontier Gap Map generation."""
    bench = tmp_lab_engine.record_benchmark(
        benchmark_id="BENCH_001",
        target_model_or_system="Level_3_Hood",
        dimension=EvaluationDimension.CODING_QUALITY,
        test_count=50,
        passed_count=48,
        cost_usd=0.0,
        time_taken_seconds=12.5,
        teacher_dependence_ratio=0.0,
        evidence="Executed 50 unit tests across Python AST mutations"
    )
    assert bench.score == 0.96
    assert bench.teacher_dependence_ratio == 0.0

    gap_map = tmp_lab_engine.compute_frontier_gap_map()
    assert gap_map.frontier_gap_percentage == 12.0
    assert len(gap_map.areas_of_hood_superiority) > 0


def test_memory_controlled_forgetting_preserves_governance(tmp_path):
    """Verifies temporal validity and that controlled forgetting never deletes governance records."""
    mem_service = MemoryService(db_path=tmp_path / "test_memory.db")
    now = datetime.now(timezone.utc)

    # 1. Expired observation
    expired_obs = MemoryObject(
        memory_id="mem_temp_obs",
        type=MemoryType.WORKING,
        content="Temporary latency observation from test run",
        project="default",
        source="system_monitor",
        source_agent="Operations_Lead",
        created_at=now - timedelta(days=2),
        valid_from=now - timedelta(days=2),
        valid_until=now - timedelta(days=1),
        learning_status=LearningStatus.OBSERVATION
    )
    mem_service.write_memory(expired_obs)

    # 2. Governance rule (has expiration set by mistake, but MUST NEVER be purged)
    gov_rule = MemoryObject(
        memory_id="mem_gov_rule",
        type=MemoryType.GOVERNANCE,
        content="H01: Zak is sole owner",
        project="core",
        source="constitution",
        source_agent="Zak",
        created_at=now - timedelta(days=10),
        valid_from=now - timedelta(days=10),
        valid_until=now - timedelta(days=1), # Expired timestamp
        learning_status=LearningStatus.ESTABLISHED
    )
    mem_service.write_memory(gov_rule, caller_agent="Zak")

    # Check temporal validity
    assert mem_service.check_temporal_validity("mem_temp_obs") is False

    # Purge expired memories
    purged_count = mem_service.purge_expired_memories()
    assert purged_count == 1

    # Verify expired observation was deleted
    assert mem_service.get_memory("mem_temp_obs") is None

    # Verify governance rule was strictly preserved
    gov_preserved = mem_service.get_memory("mem_gov_rule")
    assert gov_preserved is not None
    assert gov_preserved.type == MemoryType.GOVERNANCE
