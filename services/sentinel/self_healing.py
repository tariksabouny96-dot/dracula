"""
PROJECT SENTINEL — HOOD Defensive Self-Healing Service
Executes low-risk, reversible automated repairs within strict delegated boundaries.
Rejects unauthorized mutations to Root Owner, governance, keys, or firewall.
Governed by Master System Specification v1.3 Section 11.
"""

from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from services.sentinel.contracts import SelfHealingAction, FindingSeverity


class SelfHealingEngine:
    """Provides governed defensive self-healing for noncritical components."""

    # Explicitly forbidden from autonomous modification (Protected System Boundaries)
    IMMUTABLE_BOUNDARIES = {
        "ROOT_OWNER_IDENTITY",
        "CONSTITUTIONAL_GOVERNANCE",
        "APPROVAL_GATES",
        "AUTHENTICATION_PASSWORDS",
        "RECOVERY_KEYS",
        "FIREWALL_POLICY",
        "X_ACTIVATION_CONTROLS",
        "EMERGENCY_STOP",
        "VAULT_MASTER_KEY",
    }

    def __init__(self, actions_log: Optional[Path] = None):
        self.actions_log = actions_log or Path("artifacts/sentinel_self_healing.log")
        self.actions_history: List[SelfHealingAction] = []

    def repair_disposable_cache(self, cache_dir: Path) -> SelfHealingAction:
        """Low-risk (L1) repair: clears and rebuilds a corrupted disposable cache."""
        action = SelfHealingAction(
            target_component=str(cache_dir),
            description="Rebuild disposable cache directory",
            risk_level="L1",
            is_authorized=True
        )

        try:
            cache_dir = cache_dir.resolve()
            allowed_cache = (Path.cwd() / "artifacts" / "disposable_cache").resolve()
            if cache_dir != allowed_cache or cache_dir.is_symlink():
                raise PermissionError("Only HOOD's dedicated disposable cache can be cleared")
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            action.executed = True
            action.success = True
            action.details = f"Successfully rebuilt disposable cache at {cache_dir}"
        except Exception as e:
            action.executed = False
            action.is_authorized = False
            action.success = False
            action.details = f"Cache rebuild blocked or failed: {str(e)}"

        self._record_action(action)
        return action

    def restart_stalled_service(self, service_name: str) -> SelfHealingAction:
        """Low-risk (L1) repair: restarts an approved background worker or monitoring task."""
        action = SelfHealingAction(
            target_component=service_name,
            description=f"Restart noncritical background service: {service_name}",
            risk_level="L1",
            is_authorized=True,
            executed=False,
            success=False,
            details=f"No supervised restart executor is registered for {service_name}."
        )
        self._record_action(action)
        return action

    def attempt_governed_repair(self, target_boundary: str, proposed_repair: str) -> SelfHealingAction:
        """
        Validates repair against immutable boundaries.
        Refuses autonomous mutation of protected governance, identity, or firewall.
        """
        if target_boundary in self.IMMUTABLE_BOUNDARIES:
            action = SelfHealingAction(
                target_component=target_boundary,
                description=proposed_repair,
                risk_level="L5",
                is_authorized=False,
                executed=False,
                success=False,
                details=f"Autonomous self-healing BLOCKED by Sentinel Policy. Target '{target_boundary}' requires explicit Root Owner (ZACK) authorization."
            )
            self._record_action(action)
            return action

        # For authorized noncritical target
        action = SelfHealingAction(
            target_component=target_boundary,
            description=proposed_repair,
            risk_level="L2",
            is_authorized=True,
            executed=False,
            success=False,
            details="Repair not executed: no action-specific approved implementation is installed."
        )
        self._record_action(action)
        return action

    def _record_action(self, action: SelfHealingAction):
        self.actions_history.append(action)
        self.actions_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.actions_log, "a", encoding="utf-8") as f:
            f.write(f"[{action.timestamp}] {action.action_id} | {action.target_component} | RISK: {action.risk_level} | SUCCESS: {action.success} | {action.details}\n")
