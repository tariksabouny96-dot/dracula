"""
HOOD Unattended & Overnight Execution Manager
Governed by Milestone V0.5D Directive Sections 18-19.
Manages batch execution while Zak is asleep/away:
- Preserves blocked branches (WAITING_FOR_HUMAN, WAITING_FOR_APPROVAL).
- Allows independent branches (research, local tests, build) to continue uninterrupted.
- Compiles a comprehensive Morning Report upon completion.
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import uuid

from services.desktop.contracts import (
    UnattendedBranchState,
    MorningReport,
    VerificationChallenge
)
from services.audit.service import AuditService


class OvernightExecutionManager:
    """Coordinates unattended task branches and compiles Morning Reports."""

    def __init__(self, audit_service: Optional[AuditService] = None):
        self.audit_service = audit_service or AuditService()
        self.branch_states: Dict[str, UnattendedBranchState] = {}
        self.branch_metadata: Dict[str, Dict[str, Any]] = {}
        self.session_start = datetime.now(timezone.utc)

    def register_branch(self, branch_id: str, title: str, task_id: str, initial_state: UnattendedBranchState = UnattendedBranchState.RUNNING):
        self.branch_states[branch_id] = initial_state
        self.branch_metadata[branch_id] = {
            "title": title,
            "task_id": task_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "cost_incurred": 0.0,
            "free_fallback": False,
            "findings": []
        }

    def update_branch_state(self, branch_id: str, state: UnattendedBranchState, details: Optional[Dict[str, Any]] = None):
        if branch_id in self.branch_states:
            self.branch_states[branch_id] = state
            if details:
                self.branch_metadata[branch_id].update(details)

    def generate_morning_report(self) -> MorningReport:
        now = datetime.now(timezone.utc)
        completed = []
        failed = []
        waiting_appr = []
        waiting_human = []
        free_fallbacks = []
        total_cost = 0.0
        findings = []

        for b_id, state in self.branch_states.items():
            meta = self.branch_metadata.get(b_id, {})
            item = {"branch_id": b_id, **meta, "state": state.value}
            cost = meta.get("cost_incurred", 0.0)
            total_cost += cost

            if meta.get("findings"):
                findings.extend(meta.get("findings"))

            if meta.get("free_fallback"):
                free_fallbacks.append(item)

            if state == UnattendedBranchState.COMPLETED:
                completed.append(item)
            elif state == UnattendedBranchState.FAILED:
                failed.append(item)
            elif state == UnattendedBranchState.WAITING_FOR_APPROVAL:
                waiting_appr.append(item)
            elif state == UnattendedBranchState.WAITING_FOR_HUMAN:
                waiting_human.append(item)

        report = MorningReport(
            period_start=self.session_start,
            period_end=now,
            completed_tasks=completed,
            failed_tasks=failed,
            waiting_for_approval=waiting_appr,
            waiting_for_human_verification=waiting_human,
            cost_incurred_usd=total_cost,
            cost_recommended_not_authorized_usd=0.0,
            free_fallbacks_used=free_fallbacks,
            security_events=[],
            model_usage_summary={"level_1": "active", "level_2": "local_offline"},
            important_findings=findings
        )
        return report
