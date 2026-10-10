"""
HOOD Evolution Engine - Central Subsystem Facade
Coordinates Registry, Arena, ExperienceCollector, DatasetBuilder, PromotionController,
HardwareResourceManager, and CostAwareEvolutionRouter.
"""

from typing import Dict, Any, List, Optional
from pathlib import Path

from services.evolution.contracts import (
    ModelIdentity,
    ModelLevel,
    CapabilityDomain,
    PromotionState,
    HardwareProfile,
    ExecutionRequirement,
    GPUOwnership
)
from services.evolution.model_registry import ModelLevelRegistry
from services.evolution.experience import ExperienceCollector
from services.evolution.arena import ModelArena
from services.evolution.ranking import CapabilityRanker
from services.evolution.promotion import PromotionController
from services.evolution.drift import DriftDetector
from services.evolution.calibration import ActiveLearningSelector, TeacherCalibration
from services.evolution.dataset import DatasetBuilder
from services.evolution.hardware import HardwareResourceManager, GPUBreakEvenCalculator
from services.evolution.routing import CostAwareEvolutionRouter
from services.evolution.acceleration import Level3AccelerationEngine
from packages.config.paths import store_path, store_dir


class EvolutionEngine:
    """Unified coordinator for HOOD's 3-level model architecture and validated self-learning."""

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()
        self.registry = ModelLevelRegistry()
        self.experience_collector = ExperienceCollector(store_dir(None, "artifacts/evolution/experiences"))
        self.arena = ModelArena()
        self.ranker = CapabilityRanker()
        self.promotion_controller = PromotionController(self.registry, self.ranker)
        self.drift_detector = DriftDetector(self.registry, self.ranker)
        self.calibration = TeacherCalibration()
        self.active_learning = ActiveLearningSelector()
        self.dataset_builder = DatasetBuilder(self.experience_collector, store_dir(None, "artifacts/evolution/datasets"))
        self.hardware_mgr = HardwareResourceManager()
        self.router = CostAwareEvolutionRouter(self.registry, self.hardware_mgr)
        self.acceleration_engine = Level3AccelerationEngine(store_path(None, "artifacts/acceleration_engine.db"))

        self._init_default_models_and_hardware()

    def _init_default_models_and_hardware(self):
        # 1. Level 1 External
        self.registry.register_model(ModelIdentity(
            model_id="gemini-3.8-flash",   # gemini-2.5-* is closed to new API users (HTTP 404)
            version="2026.10",
            provider_runtime="gemini_api",
            level=ModelLevel.LEVEL_1_EXTERNAL,
            capabilities=[CapabilityDomain.CODING, CapabilityDomain.RESEARCH, CapabilityDomain.GENERAL_REASONING, CapabilityDomain.COMMERCE],
            context_window=1000000,
            # Published paid-tier price on 2026-10-09 (planning figure only; billing uses the
            # owner's price file). Was a stale gemini-2.5-flash price.
            cost_per_1k_input=0.00075,
            cost_per_1k_output=0.00375,
            is_privacy_compliant=False,
            promotion_state=PromotionState.PRIMARY
        ))

        # 2. Level 2 Self-Hosted (Ollama / vLLM / llama.cpp)
        self.registry.register_model(ModelIdentity(
            model_id="llama3-8b-local",
            version="3.1-8b-instruct",
            provider_runtime="ollama_local",
            level=ModelLevel.LEVEL_2_SELF_HOSTED,
            capabilities=[CapabilityDomain.CODING, CapabilityDomain.STRUCTURED_EXTRACTION, CapabilityDomain.TOOL_USE],
            context_window=8192,
            min_execution_req=ExecutionRequirement(min_vram_gb=8.0, requires_single_device=True),
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            is_privacy_compliant=True,
            promotion_state=PromotionState.SECONDARY
        ))

        # 3. Level 3 HOOD Specialized Candidate (Starts strictly in SHADOW)
        self.registry.register_model(ModelIdentity(
            model_id="hood-code-candidate-v1",
            version="v0.5c-can-1",
            provider_runtime="hood_adapter",
            level=ModelLevel.LEVEL_3_HOOD,
            capabilities=[CapabilityDomain.CODING, CapabilityDomain.TOOL_USE],
            context_window=16384,
            min_execution_req=ExecutionRequirement(min_vram_gb=12.0, requires_single_device=True),
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            is_privacy_compliant=True,
            promotion_state=PromotionState.SHADOW,
            base_model="llama3-8b-local",
            training_recipe="qlora_coding_v1"
        ))

        # Register local dev hardware profile (simulated workstation with 16GB GPU for local dev testing)
        self.hardware_mgr.register_hardware("local_dev", HardwareProfile(
            profile_id="hw_local_workstation",
            gpu_vendor="NVIDIA",
            gpu_model="RTX 4080 (Simulated)",
            gpu_count=1,
            per_device_vram_gb=[16.0],
            aggregate_vram_gb=16.0,
            system_ram_gb=32.0,
            cpu_cores=8,
            disk_free_gb=150.0,
            cuda_available=True,
            ownership=GPUOwnership.OWNED
        ))

        # Register rented GPU cluster profile (for testing routing without purchasing)
        self.hardware_mgr.register_hardware("rented_gpu_cluster", HardwareProfile(
            profile_id="hw_rented_a100",
            gpu_vendor="NVIDIA",
            gpu_model="A100",
            gpu_count=1,
            per_device_vram_gb=[40.0],
            aggregate_vram_gb=40.0,
            system_ram_gb=64.0,
            cpu_cores=16,
            disk_free_gb=200.0,
            cuda_available=True,
            ownership=GPUOwnership.RENTED,
            cost_per_hour_usd=1.85
        ))
