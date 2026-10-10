"""
HOOD Cost Controller
Enforces per-task, daily, and monthly spend thresholds and usage accounting.
Governed by Master System Specification Section 8.5 & Build Instructions Section 10.
"""

import contextlib
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
import sys
from datetime import datetime, timedelta, timezone
from packages.config import BudgetSettings
from packages.contracts import ModelUsage


class BudgetExceededError(Exception):
    """Raised when an operation would exceed configured spending or call limits."""
    pass


class CostController:
    """Spend caps and usage accounting.

    With ``ledger_path`` every settled call is written to a SQLite ledger and today's/this month's
    spend are reloaded from it at start-up: restarting HOOD no longer resets the daily and monthly
    caps to zero (security batch 1), and the usage history survives for the owner to check."""

    def __init__(self, budgets: Optional[BudgetSettings] = None, ledger_path: Optional[Path] = None):
        self.budgets = budgets or BudgetSettings()
        self.task_spend: Dict[str, float] = {}
        self.task_calls: Dict[str, int] = {}
        self.task_deep_calls: Dict[str, int] = {}
        self.daily_spend_usd: float = 0.0
        self.monthly_spend_usd: float = 0.0
        self.total_tokens_consumed: int = 0
        self.call_history: List[Dict[str, Any]] = []
        self._last_reset_date = datetime.now(timezone.utc).date()
        self._last_reset_month = (self._last_reset_date.year, self._last_reset_date.month)
        self._lock = threading.RLock()
        # reservation_id -> {task_id, amount, is_deep}
        self._reservations: Dict[str, Dict[str, Any]] = {}
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.ledger_error: Optional[str] = None
        if self.ledger_path is not None:
            self._open_ledger()

    # ------------------------------------------------------------------ durable ledger
    @contextlib.contextmanager
    def _ledger(self):
        db = sqlite3.connect(str(self.ledger_path), timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _open_ledger(self) -> None:
        try:
            self._load_ledger()
        except (OSError, sqlite3.Error) as exc:
            sys.stderr.write(f"HOOD spend ledger unavailable ({exc}); spend is tracked in memory only.\n")
            self.ledger_error = str(exc)

    def _load_ledger(self) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self._ledger() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
                ts TEXT NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, task_id TEXT, provider TEXT,
                model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
                cost_usd REAL NOT NULL, reserved_usd REAL, cost_measured INTEGER, price_source TEXT,
                is_fallback INTEGER)""")
            today = self._last_reset_date
            month = f"{today.year:04d}-{today.month:02d}"
            self.daily_spend_usd = db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls WHERE day=?",
                                              (today.isoformat(),)).fetchone()[0]
            self.monthly_spend_usd = db.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls WHERE month=?",
                                                (month,)).fetchone()[0]
            self.total_tokens_consumed = db.execute("SELECT COALESCE(SUM(total_tokens),0) FROM calls").fetchone()[0]
            # Per-task spend and call counts of recent tasks (a mission resumed after a restart keeps its caps).
            since = (today - timedelta(days=2)).isoformat()
            for task_id, spent, calls in db.execute(
                    "SELECT task_id, SUM(cost_usd), COUNT(*) FROM calls WHERE task_id IS NOT NULL AND day >= ? "
                    "GROUP BY task_id", (since,)):
                self.task_spend[task_id] = spent
                self.task_calls[task_id] = calls

    def _persist(self, record: Dict[str, Any]) -> None:
        if self.ledger_path is None or self.ledger_error:
            return
        try:
            self._insert(record)
        except (OSError, sqlite3.Error) as exc:
            # Never lose a paid answer over a ledger write: keep counting in memory and say so.
            self.ledger_error = str(exc)
            sys.stderr.write(f"HOOD spend ledger write failed ({exc}); spend is tracked in memory only.\n")

    def _insert(self, record: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        with self._ledger() as db:
            db.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (record["timestamp"], now.date().isoformat(), f"{now.year:04d}-{now.month:02d}",
                        record.get("task_id"), record.get("provider"), record.get("model"),
                        record.get("prompt_tokens"), record.get("completion_tokens"), record.get("total_tokens"),
                        record["cost_usd"], record.get("reserved_usd"), int(bool(record.get("cost_measured", True))),
                        record.get("price_source"), int(bool(record.get("is_fallback")))))

    def _check_and_reset_daily(self):
        current_date = datetime.now(timezone.utc).date()
        if current_date != self._last_reset_date:
            self.daily_spend_usd = 0.0
            self._last_reset_date = current_date
        if (current_date.year, current_date.month) != self._last_reset_month:
            self.monthly_spend_usd = 0.0
            self._last_reset_month = (current_date.year, current_date.month)

    def _reserved(self, task_id: Optional[str] = None) -> float:
        return sum(r["amount"] for r in self._reservations.values()
                   if task_id is None or r["task_id"] == task_id)

    def reserve(self, task_id: Optional[str], amount_usd: float, is_deep_model: bool = False) -> str:
        """Atomically reserve worst-case spend and one call slot before a paid request.

        Concurrent callers cannot jointly exceed a cap: reservations count against
        every limit until settled or released.
        """
        if amount_usd < 0:
            raise ValueError("Reservation amount must be non-negative")
        with self._lock:
            self._check_and_reset_daily()
            if self.budgets.hard_stop_on_budget_exceeded:
                pending = self._reserved()
                if self.daily_spend_usd + pending + amount_usd > self.budgets.max_daily_spend_usd:
                    raise BudgetExceededError("Daily spend cap would be exceeded")
                if self.monthly_spend_usd + pending + amount_usd > self.budgets.max_monthly_spend_usd:
                    raise BudgetExceededError("Monthly spend cap would be exceeded")
                if task_id:
                    if self.task_spend.get(task_id, 0.0) + self._reserved(task_id) + amount_usd > self.budgets.max_task_spend_usd:
                        raise BudgetExceededError(f"Task {task_id} spend cap would be exceeded")
                    if self.task_calls.get(task_id, 0) >= self.budgets.max_task_model_calls:
                        raise BudgetExceededError(f"Task {task_id} model call cap reached")
                    if is_deep_model and self.task_deep_calls.get(task_id, 0) >= self.budgets.max_task_deep_model_calls:
                        raise BudgetExceededError(f"Task {task_id} deep model call cap reached")
            rid = uuid.uuid4().hex
            self._reservations[rid] = {"task_id": task_id, "amount": amount_usd, "is_deep": is_deep_model}
            # The call slot is consumed at reservation time so parallel calls cannot overrun it.
            if task_id:
                self.task_calls[task_id] = self.task_calls.get(task_id, 0) + 1
                if is_deep_model:
                    self.task_deep_calls[task_id] = self.task_deep_calls.get(task_id, 0) + 1
            return rid

    def release(self, reservation_id: str) -> None:
        """Return a reservation for a request that was provably never sent."""
        with self._lock:
            r = self._reservations.pop(reservation_id, None)
            if r and r["task_id"]:
                self.task_calls[r["task_id"]] = max(0, self.task_calls.get(r["task_id"], 1) - 1)
                if r["is_deep"]:
                    self.task_deep_calls[r["task_id"]] = max(0, self.task_deep_calls.get(r["task_id"], 1) - 1)

    def settle(self, reservation_id: str, usage: ModelUsage, *, cost_measured: bool,
               provider: str = "unknown", model: str = "unknown", latency_ms: int = 0,
               is_fallback: bool = False, price_source: Optional[str] = None) -> float:
        """Convert a reservation into recorded spend.

        If the provider did not report usage (``cost_measured`` False) the full
        reservation is charged; missing usage is never recorded as zero.
        """
        with self._lock:
            r = self._reservations.pop(reservation_id, None)
            if r is None:
                raise KeyError("Unknown or already settled reservation")
            cost = usage.estimated_cost_usd if cost_measured else r["amount"]
            self._check_and_reset_daily()
            self.daily_spend_usd += cost
            self.monthly_spend_usd += cost
            self.total_tokens_consumed += usage.total_tokens
            task_id = r["task_id"]
            if task_id:
                self.task_spend[task_id] = self.task_spend.get(task_id, 0.0) + cost
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "task_id": task_id,
                "provider": provider, "model": model, "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens, "total_tokens": usage.total_tokens,
                "cost_usd": cost, "reserved_usd": r["amount"], "cost_measured": cost_measured,
                "price_source": price_source, "is_free_tier": False, "billing_verified": False,
                "latency_ms": latency_ms, "is_deep_model": r["is_deep"], "is_fallback": is_fallback}
            self.call_history.append(record)
            del self.call_history[:-1000]          # bounded in memory; the ledger keeps everything
            self._persist(record)
            return cost

    def can_execute(
        self,
        task_id: Optional[str] = None,
        estimated_cost_usd: float = 0.0,
        is_deep_model: bool = False
    ) -> bool:
        self._check_and_reset_daily()

        if not self.budgets.hard_stop_on_budget_exceeded:
            return True

        pending = self._reserved()
        if self.daily_spend_usd + pending + estimated_cost_usd > self.budgets.max_daily_spend_usd:
            return False

        if self.monthly_spend_usd + pending + estimated_cost_usd > self.budgets.max_monthly_spend_usd:
            return False

        if task_id:
            current_task_spend = self.task_spend.get(task_id, 0.0) + self._reserved(task_id)
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
        with self._lock:
            self._record_usage_locked(task_id, usage, is_deep_model, provider, model, latency_ms, is_fallback)

    def _record_usage_locked(self, task_id, usage, is_deep_model, provider, model, latency_ms, is_fallback):
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
        del self.call_history[:-1000]
        self._persist(call_record)

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
            "reserved_usd": round(self._reserved(), 4),
            "monthly_spend_usd": round(self.monthly_spend_usd, 4),
            "total_tokens_consumed": self.total_tokens_consumed,
            "total_calls_recorded": len(self.call_history),
            "max_daily_limit_usd": self.budgets.max_daily_spend_usd,
            "max_monthly_limit_usd": self.budgets.max_monthly_spend_usd,
            "ledger": str(self.ledger_path) if self.ledger_path else None,
            "ledger_error": self.ledger_error,
        }
