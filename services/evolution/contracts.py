"""
HOOD Evolution Engine - Core Contracts and Data Models
Authoritative Schemas for Level 1/2/3 Architecture, Arena, Experience, and Hardware Routing.
Governed by Master System Specification Sections 2, 8, 14 & V0.5C Evolution Engine Directives.
"""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field


# --- Enums ---

class ModelLevel(str, Enum):
    LEVEL_1_EXTERNAL = "LEVEL_1_EXTERNAL"
    LEVEL_2_SELF_HOSTED = "LEVEL_2_SELF_HOSTED"
    LEVEL_3_HOOD = "LEVEL_3_HOOD"


class PromotionState(str, Enum):
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    SPECIALIST = "SPECIALIST"
    SECONDARY = "SECONDARY"
    CO_PRIMARY = "CO_PRIMARY"
    PRIMARY = "PRIMARY"
    SUSPENDED = "SUSPENDED"


class CapabilityDomain(str, Enum):
    CODING = "CODING"
    RESEARCH = "RESEARCH"
    COMMERCE = "COMMERCE"
    SECURITY = "SECURITY"
    TOOL_USE = "TOOL_USE"
    ROUTING = "ROUTING"
    DATA_ANALYSIS = "DATA_ANALYSIS"
    VOICE_REASONING = "VOICE_REASONING"
    STRUCTURED_EXTRACTION = "STRUCTURED_EXTRACTION"
    GENERAL_REASONING = "GENERAL_REASONING"


class EvidenceQuality(str, Enum):
    CONFIRMED_DETERMINISTIC = "CONFIRMED_DETERMINISTIC"   # Verified by unit/regression test or exact execution
    PRIMARY_SOURCE = "PRIMARY_SOURCE"                     # Direct file/API/authoritative documentation citation
    MAKER_CHECKER_VERIFIED = "MAKER_CHECKER_VERIFIED"     # Independent secondary validator approved
    MULTI_MODEL_CONSENSUS = "MULTI_MODEL_CONSENSUS"       # Multiple models agreed without collusion
    UNVERIFIED_HEURISTIC = "UNVERIFIED_HEURISTIC"         # Unverified model speculation
    DISPROVEN_CONTRADICTED = "DISPROVEN_CONTRADICTED"     # Refuted by test failure or ground truth


class FailureCategory(str, Enum):
    WRONG_ANSWER = "WRONG_ANSWER"
    HALLUCINATION = "HALLUCINATION"
    TOOL_FAILURE = "TOOL_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    POLICY_FAILURE = "POLICY_FAILURE"
    SECURITY_FAILURE = "SECURITY_FAILURE"
    TIMEOUT = "TIMEOUT"
    RESOURCE_FAILURE = "RESOURCE_FAILURE"
    COST_OVERRUN = "COST_OVERRUN"
    USER_CORRECTION = "USER_CORRECTION"
    EVIDENCE_FAILURE = "EVIDENCE_FAILURE"
    ROUTING_FAILURE = "ROUTING_FAILURE"


class DatasetPrivacyScope(str, Enum):
    GLOBAL_SANITIZED = "GLOBAL_SANITIZED"
    PROJECT_ONLY = "PROJECT_ONLY"
    PERSONAL_PRIVATE = "PERSONAL_PRIVATE"
    DO_NOT_TRAIN = "DO_NOT_TRAIN"
    X_SEALED = "X_SEALED"


class GPUOwnership(str, Enum):
    OWNED = "OWNED"
    RENTED = "RENTED"
    NONE = "NONE"


# --- Hardware & Resource Contracts ---

class HardwareProfile(BaseModel):
    """
    Hardware resource representation.
    CRITICAL: 2x16GB is NEVER treated as a seamless 32GB device.
    per_device_vram and topology are tracked explicitly.
    """
    profile_id: str = Field(default_factory=lambda: f"hw_{uuid.uuid4().hex[:8]}")
    gpu_vendor: Optional[str] = None
    gpu_model: Optional[str] = None
    gpu_count: int = 0
    per_device_vram_gb: List[float] = Field(default_factory=list)
    aggregate_vram_gb: float = 0.0
    system_ram_gb: float = 16.0
    cpu_cores: int = 4
    disk_free_gb: float = 100.0
    cuda_available: bool = False
    ownership: GPUOwnership = GPUOwnership.NONE
    cost_per_hour_usd: float = 0.0
    
    # Topology and Parallelism capabilities
    model_parallel_capable: bool = False
    tensor_parallel_capable: bool = False
    pipeline_parallel_capable: bool = False
    independent_workload_capable: bool = True

    def max_single_device_vram(self) -> float:
        return max(self.per_device_vram_gb) if self.per_device_vram_gb else 0.0


class ExecutionRequirement(BaseModel):
    min_vram_gb: float = 0.0
    requires_single_device: bool = True
    min_ram_gb: float = 8.0
    privacy_mandate: bool = False
    max_latency_ms: float = 10000.0


# --- Model Identity & Registry Contracts ---

class ModelIdentity(BaseModel):
    model_id: str
    version: str
    provider_runtime: str
    level: ModelLevel
    capabilities: List[CapabilityDomain] = Field(default_factory=list)
    context_window: int = 8192
    min_execution_req: ExecutionRequirement = Field(default_factory=ExecutionRequirement)
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    is_privacy_compliant: bool = False
    available: bool = True
    promotion_state: PromotionState = PromotionState.SHADOW
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_evaluated_at: Optional[str] = None
    
    # Lineage tracking
    base_model: Optional[str] = None
    lineage_parent: Optional[str] = None
    dataset_version: Optional[str] = None
    training_recipe: Optional[str] = None
    benchmark_version: Optional[str] = None


# --- Arena & Evaluation Contracts ---

class ArenaTask(BaseModel):
    task_id: str = Field(default_factory=lambda: f"arena_{uuid.uuid4().hex[:8]}")
    domain: CapabilityDomain
    prompt: str
    ground_truth_test: Optional[str] = None  # Executable code, regex, or deterministic function ref
    deterministic_expected_output: Optional[str] = None
    risk_level: str = "L1"
    project_context: str = "general"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ArenaAttempt(BaseModel):
    attempt_id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:8]}")
    task_id: str
    model_id: str
    level: ModelLevel
    output_text: str
    latency_ms: float
    token_usage: Dict[str, int] = Field(default_factory=dict)
    cost_usd: float = 0.0
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvaluationResult(BaseModel):
    attempt_id: str
    model_id: str
    domain: CapabilityDomain
    correctness_score: float = Field(ge=0.0, le=1.0)
    evidence_quality: EvidenceQuality
    deterministic_test_passed: bool
    checker_verdict: str = "PASSED"
    policy_compliant: bool = True
    latency_ms: float
    cost_usd: float
    failure_category: Optional[FailureCategory] = None
    evaluator_comment: str = ""
    evaluated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --- Experience Record ---

class TeacherContribution(BaseModel):
    teacher_model_id: str
    level: ModelLevel
    proposed_solution: str
    was_accepted: bool
    historical_domain_reliability: float = 0.5


class ExperienceRecord(BaseModel):
    experience_id: str = Field(default_factory=lambda: f"exp_{uuid.uuid4().hex[:8]}")
    task_type: CapabilityDomain
    project_context_id: str
    sanitized_prompt: str
    level_1_attempt: Optional[ArenaAttempt] = None
    level_2_attempt: Optional[ArenaAttempt] = None
    level_3_attempt: Optional[ArenaAttempt] = None
    teacher_contributions: List[TeacherContribution] = Field(default_factory=list)
    deterministic_test_result: Optional[bool] = None
    evidence_quality: EvidenceQuality
    checker_verdict: str
    actual_task_outcome: str  # "SUCCESS", "FAILURE", "ABORTED"
    selected_final_action: str
    selection_rationale: str
    confidence: float
    privacy_scope: DatasetPrivacyScope
    failure_category: Optional[FailureCategory] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    provenance_hash: str = ""


# --- Dataset & Training Manifest Contracts ---

class TrainingDatasetManifest(BaseModel):
    dataset_id: str
    version: str
    domain: CapabilityDomain
    record_count: int
    allowed_privacy_scope: DatasetPrivacyScope
    records_hash: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    secrets_sanitized: bool = True
    x_sealed_strictly_excluded: bool = True


# --- Promotion & Drift Contracts ---

class PromotionDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"prom_{uuid.uuid4().hex[:8]}")
    model_id: str
    domain: CapabilityDomain
    from_state: PromotionState
    to_state: PromotionState
    benchmark_version: str
    composite_score: float
    approved_by: str = "POLICY_ENGINE"
    is_human_authorized: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rationale: str


class DriftObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: f"drift_{uuid.uuid4().hex[:8]}")
    model_id: str
    domain: CapabilityDomain
    baseline_score: float
    current_score: float
    drop_percentage: float
    is_demotion_recommended: bool
    demotion_target_state: Optional[PromotionState] = None
    detected_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --- GPU Break-Even & Accounting Contracts ---

class GPUBreakEvenAccounting(BaseModel):
    hardware_name: str
    purchase_price_usd: float
    depreciation_period_months: int
    monthly_depreciation_usd: float
    average_power_draw_watts: float
    electricity_cost_kwh_usd: float
    monthly_utilization_hours: float
    monthly_electricity_cost_usd: float
    monthly_owned_cost_total_usd: float
    
    equivalent_rental_rate_hour_usd: float
    monthly_rented_equivalent_cost_usd: float
    
    equivalent_api_cost_monthly_usd: float
    
    break_even_vs_rental_months: float
    break_even_vs_api_months: float
    purchase_recommended: bool
    rationale: str
