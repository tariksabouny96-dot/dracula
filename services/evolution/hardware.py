"""
HOOD Evolution Engine - Hardware Resource Manager & GPU Break-Even Calculator
Governed by Master System Specification Sections 2, 8, 14 & V0.5C Directives.

CRITICAL RULES:
- 2x16GB is NEVER treated as a seamless single 32GB device.
- Rented GPU compute is fully structured behind abstract interfaces with spending approval gates.
- Break-even calculations use configurable financial and operational parameters.
"""

from typing import Dict, Any, List, Optional
import math
from services.evolution.contracts import (
    HardwareProfile,
    ExecutionRequirement,
    GPUOwnership,
    GPUBreakEvenAccounting
)


class HardwareResourceManager:
    """Manages local and remote hardware topologies, detecting placement feasibility."""

    def __init__(self, profiles: Optional[Dict[str, HardwareProfile]] = None):
        self.profiles: Dict[str, HardwareProfile] = profiles or {}

    def register_hardware(self, name: str, profile: HardwareProfile):
        self.profiles[name] = profile

    def evaluate_placement(self, hardware_name: str, req: ExecutionRequirement) -> Dict[str, Any]:
        """
        Evaluates whether a given execution requirement can fit on a specific hardware topology.
        Enforces that multi-device hardware does NOT pool memory for single-device workloads.
        """
        profile = self.profiles.get(hardware_name)
        if not profile:
            return {"fit": False, "reason": f"Hardware profile '{hardware_name}' not found."}

        # Check system RAM
        if profile.system_ram_gb < req.min_ram_gb:
            return {"fit": False, "reason": f"Insufficient system RAM: {profile.system_ram_gb}GB < {req.min_ram_gb}GB"}

        # If zero VRAM required (CPU workload)
        if req.min_vram_gb <= 0.0:
            return {"fit": True, "device": "CPU", "parallel_mode": "CPU_THREADED"}

        # Check GPU availability
        if profile.gpu_count == 0 or not profile.per_device_vram_gb:
            return {"fit": False, "reason": f"Workload requires {req.min_vram_gb}GB VRAM but hardware has no GPU."}

        # Single-device requirement check
        if req.requires_single_device:
            max_vram = profile.max_single_device_vram()
            if max_vram < req.min_vram_gb:
                return {
                    "fit": False,
                    "reason": f"Single-device requirement not met: Largest GPU has {max_vram}GB, workload requires {req.min_vram_gb}GB. Memory pooling across multi-GPU is disallowed for single-device tasks."
                }
            return {
                "fit": True,
                "device": "SINGLE_GPU",
                "assigned_vram_gb": max_vram,
                "parallel_mode": "NONE"
            }

        # Multi-device distributed requirement
        if not (profile.model_parallel_capable or profile.tensor_parallel_capable or profile.pipeline_parallel_capable):
            max_vram = profile.max_single_device_vram()
            if max_vram < req.min_vram_gb:
                return {
                    "fit": False,
                    "reason": "Hardware lacks model/tensor/pipeline parallel capability for multi-GPU distribution."
                }

        if profile.aggregate_vram_gb < req.min_vram_gb:
            return {
                "fit": False,
                "reason": f"Aggregate VRAM insufficient: {profile.aggregate_vram_gb}GB < {req.min_vram_gb}GB"
            }

        return {
            "fit": True,
            "device": "MULTI_GPU",
            "aggregate_vram_gb": profile.aggregate_vram_gb,
            "parallel_mode": "TENSOR_PARALLEL" if profile.tensor_parallel_capable else "MODEL_PARALLEL"
        }


class GPUBreakEvenCalculator:
    """Computes evidence-backed break-even accounting between API usage, rented GPU, and owned GPU."""

    @staticmethod
    def calculate_break_even(
        hardware_name: str,
        purchase_price_usd: float,
        depreciation_period_months: int,
        average_power_draw_watts: float,
        electricity_cost_kwh_usd: float,
        monthly_utilization_hours: float,
        equivalent_rental_rate_hour_usd: float,
        equivalent_api_cost_monthly_usd: float
    ) -> GPUBreakEvenAccounting:
        # Monthly depreciation
        monthly_depreciation = purchase_price_usd / max(1, depreciation_period_months)

        # Monthly electricity cost: (watts / 1000) * hours * rate
        kwh_per_month = (average_power_draw_watts / 1000.0) * monthly_utilization_hours
        monthly_electricity = kwh_per_month * electricity_cost_kwh_usd

        # Total monthly cost of ownership (depreciation + power)
        monthly_owned_total = round(monthly_depreciation, 2) + round(monthly_electricity, 2)

        # Equivalent monthly rental cost
        monthly_rental_total = equivalent_rental_rate_hour_usd * monthly_utilization_hours

        # Break-even vs Rental in months: purchase_price / (monthly_rental - monthly_electricity)
        rental_monthly_savings = monthly_rental_total - monthly_electricity
        if rental_monthly_savings > 0:
            break_even_vs_rental = round(purchase_price_usd / rental_monthly_savings, 1)
        else:
            break_even_vs_rental = 999.0  # Never breaks even

        # Break-even vs API in months: purchase_price / (monthly_api - monthly_electricity)
        api_monthly_savings = equivalent_api_cost_monthly_usd - monthly_electricity
        if api_monthly_savings > 0:
            break_even_vs_api = round(purchase_price_usd / api_monthly_savings, 1)
        else:
            break_even_vs_api = 999.0

        # Recommendation logic:
        # If break-even is reached in under half the depreciation lifetime and utilization > 100 hours/mo
        recommended = (
            break_even_vs_rental < (depreciation_period_months * 0.75) and
            break_even_vs_api < (depreciation_period_months * 0.75) and
            monthly_utilization_hours >= 100.0
        )

        rationale = (
            f"Break-even vs API: {break_even_vs_api} mos | vs Rental: {break_even_vs_rental} mos. "
            f"Monthly owned run cost ${round(monthly_owned_total, 2)} vs API ${round(equivalent_api_cost_monthly_usd, 2)}. "
            f"Recommendation: {'PURCHASE' if recommended else 'DEFER / USE RENTAL OR API'}."
        )

        return GPUBreakEvenAccounting(
            hardware_name=hardware_name,
            purchase_price_usd=purchase_price_usd,
            depreciation_period_months=depreciation_period_months,
            monthly_depreciation_usd=round(monthly_depreciation, 2),
            average_power_draw_watts=average_power_draw_watts,
            electricity_cost_kwh_usd=electricity_cost_kwh_usd,
            monthly_utilization_hours=monthly_utilization_hours,
            monthly_electricity_cost_usd=round(monthly_electricity, 2),
            monthly_owned_cost_total_usd=round(monthly_owned_total, 2),
            equivalent_rental_rate_hour_usd=equivalent_rental_rate_hour_usd,
            monthly_rented_equivalent_cost_usd=round(monthly_rental_total, 2),
            equivalent_api_cost_monthly_usd=equivalent_api_cost_monthly_usd,
            break_even_vs_rental_months=break_even_vs_rental,
            break_even_vs_api_months=break_even_vs_api,
            purchase_recommended=recommended,
            rationale=rationale
        )
