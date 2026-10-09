"""
HOOD Cost Controller
Enforces per-task, daily, and monthly spend thresholds and usage accounting.
Governed by Master System Specification Section 8.5 & Build Instructions Section 10.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from packages.config import BudgetSettings
from packages.contracts import ModelUsage


class BudgetExceededError(Exception):
    """Raised when an operation would exceed configured spending or call limits."""
    pass


class CostController:
    def __init__(self, budgets: Optional[BudgetSettings] = None):
        self.budgets = budgets or BudgetSettings()
        self.task_spend: Dict[str, float] = {}
        self.task_calls: Dict[str, int] = {}
        self.task_deep_calls: Dict[str, int] = {}
        self.daily_spend_usd: float = 0.0
        self.monthly_spend_usd: float = 0.0
        self.total_tokens_consumed: int = 0
        self.call_history: List[Dict[str, Any]] = []
        self._last_reset_date = datetime.now(timezone.utc).date()

    def _check_and_reset_daily(self):
        current_date = datetime.now(timezone.utc).date()
        if current_date != self._last_reset_date:
            self.daily_spend_usd = 0.0
            self._last_reset_date = current_date

    def can_execute(
        self,
        task_id: Optional[str] = None,
        estimated_cost_usd: float = 0.0,
        is_deep_model: bool = False
    ) -> bool:
        self._check_and_reset_daily()

        if not self.budgets.hard_stop_on_budget_exceeded:
            return True

        if self.daily_spend_usd + estimated_cost_usd > self.budgets.max_daily_spend_usd:
            return False

        if self.monthly_spend_usd + estimated_cost_usd > self.budgets.max_monthly_spend_usd:
            return False

        if task_id:
            current_task_spend = self.task_spend.get(task_id, 0.0)
            if current_task_spend + estimated_cost_usd > self.budgets.max_task_spend_usd:
                return False

            current_calls = self.task_calls.get(task_id, 0)
            if current_calls >= self.budgets.max_task_model_calls:
                return False

            if is_deep_model:
                current_deep = self.task_deep_calls.get(task_id, 0)
                if current_deep >= self.budgets.max_task_deep_model_calls:
                    return False

        return True

    def record_usage(
        self,
        task_id: Optional[str],
        usage: ModelUsage,
        is_deep_model: bool = False,
        provider: str = "unknown",
        model: str = "unknown",
        latency_ms: int = 0,
        is_fallback: bool = False
    ) -> None:
        self._check_and_reset_daily()
        cost = usage.estimated_cost_usd

        self.daily_spend_usd += cost
        self.monthly_spend_usd += cost
        self.total_tokens_consumed += usage.total_tokens

        # Record detailed event
        call_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "provider": provider,
            "model": model,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost_usd": cost,
            "is_free_tier": False,  # Zero estimated cost is not proof of free-tier billing.
            "billing_verified": False,
            "latency_ms": latency_ms,
            "is_deep_model": is_deep_model,
            "is_fallback": is_fallback
        }
        self.call_history.append(call_record)

        if task_id:
            self.task_spend[task_id] = self.task_spend.get(task_id, 0.0) + cost
            self.task_calls[task_id] = self.task_calls.get(task_id, 0) + 1
            if is_deep_model:
                self.task_deep_calls[task_id] = self.task_deep_calls.get(task_id, 0) + 1

    def get_task_spend(self, task_id: str) -> float:
        return self.task_spend.get(task_id, 0.0)

    def get_call_history(self, task_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if task_id:
            return [c for c in self.call_history if c["task_id"] == task_id]
        return list(self.call_history)

    def get_summary(self) -> dict:
        self._check_and_reset_daily()
        return {
            "daily_spend_usd": round(self.daily_spend_usd, 4),
            "monthly_spend_usd": round(self.monthly_spend_usd, 4),
            "total_tokens_consumed": self.total_tokens_consumed,
            "total_calls_recorded": len(self.call_history),
            "max_daily_limit_usd": self.budgets.max_daily_spend_usd,
            "max_monthly_limit_usd": self.budgets.max_monthly_spend_usd,
        }
