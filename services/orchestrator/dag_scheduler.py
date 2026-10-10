"""
HOOD Task Orchestrator & DAG Scheduler
Governed by Master System Specification Section 4, 14 & Build Instructions Section 11.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime, timezone
import time

from packages.contracts import (
    TaskNode,
    TaskStatus,
    RiskLevel,
    ApprovalStatus,
    AgentResponseContract,
    EvidencePacket,
    AuditEvent
)
from packages.config import SystemConfig
from services.policy.governance import RiskEvaluator
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from services.core.emergency_stop import EmergencyStopController


class EmergencyStopTriggeredError(Exception):
    """Raised when an operation is halted by Emergency Stop."""
    pass


class CyclicDependencyError(Exception):
    """Raised when task DAG contains circular dependencies."""
    pass


class TaskExecutionBlockedError(Exception):
    """Raised when a task is blocked by unapproved risk gate or failed dependency."""
    pass


class DAGOrchestrator:
    """Manages DAG execution, parallel dependency resolution, approval pauses, and emergency stop."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        approval_service: Optional[ApprovalService] = None,
        audit_service: Optional[AuditService] = None,
        emergency_stop: Optional[EmergencyStopController] = None
    ):
        self.config = config or SystemConfig()
        self.approval_service = approval_service or ApprovalService()
        self.audit_service = audit_service or AuditService()
        self.emergency_stop = emergency_stop
        self.tasks: Dict[str, TaskNode] = {}

    def add_task(self, task: TaskNode):
        self.tasks[task.task_id] = task

    def validate_dag(self):
        """Detects circular dependencies in the registered tasks."""
        visited = set()
        rec_stack = set()

        def dfs(node_id: str):
            visited.add(node_id)
            rec_stack.add(node_id)
            node = self.tasks.get(node_id)
            if node:
                for dep_id in node.dependencies:
                    if dep_id not in visited:
                        if dfs(dep_id):
                            return True
                    elif dep_id in rec_stack:
                        return True
            rec_stack.remove(node_id)
            return False

        for t_id in self.tasks:
            if t_id not in visited:
                if dfs(t_id):
                    raise CyclicDependencyError(f"Circular dependency detected involving task '{t_id}'.")

    def run_dag(self, task_executor: Callable[[TaskNode], AgentResponseContract]) -> Dict[str, TaskNode]:
        """Executes the task DAG respecting dependencies, approval gates, and emergency stop."""
        self.validate_dag()

        completed_ids = set()
        failed_ids = set()

        while len(completed_ids) + len(failed_ids) < len(self.tasks):
            # Check Emergency Stop
            if self.emergency_stop and self.emergency_stop.is_active:
                for t in self.tasks.values():
                    if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                        t.status = TaskStatus.EMERGENCY_STOPPED
                raise EmergencyStopTriggeredError("Task DAG execution halted by Emergency Stop.")

            # Find ready tasks: all dependencies completed
            ready_tasks = [
                t for t in self.tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.WAITING_APPROVAL)
                and all(dep in completed_ids for dep in t.dependencies)
            ]

            if not ready_tasks:
                break

            # Execute ready tasks
            progress_made = False
            for task in ready_tasks:
                # 1. Evaluate Risk Gate
                risk = task.risk_level
                if RiskEvaluator.requires_approval(risk, self.config):
                    appr_id = task.inputs.get("approval_id")
                    # Check if approval was explicitly rejected
                    if appr_id and self.approval_service.is_rejected(appr_id):
                        task.status = TaskStatus.FAILED
                        task.error = f"APPROVAL_REJECTED: Task '{task.title}' was rejected by Zak/Security Policy."
                        failed_ids.add(task.task_id)
                        progress_made = True
                        self.audit_service.record_event(AuditEvent(
                            actor=task.assigned_agent,
                            task_id=task.task_id,
                            project=task.project,
                            action=f"task_rejected:{task.title}",
                            target=task.title,
                            policy_decision="DENY",
                            approval_ref=appr_id,
                            error=task.error,
                            verification="FAILED"
                        ))
                        raise TaskExecutionBlockedError(f"APPROVAL_REJECTED: Task '{task.title}' approval was rejected.")

                    if not (appr_id and self.approval_service.is_approved(appr_id)):
                        task.status = TaskStatus.WAITING_APPROVAL
                        if not appr_id:
                            appr_req = self.approval_service.create_request(
                                task_id=task.task_id,
                                action_type="task_execution",
                                target=task.title,
                                reason=f"Task objective: {task.objective}",
                                risk_level=risk,
                                recommended_option="Approve execution"
                            )
                            task.inputs["approval_id"] = appr_req.approval_id
                        continue

                # 2. Run task
                progress_made = True
                task.status = TaskStatus.RUNNING
                try:
                    start_time = time.time()
                    resp = task_executor(task)
                    resp.execution_time_ms = int((time.time() - start_time) * 1000)

                    # H05 / Maker-Checker rule: Consequential work requires independent verification
                    if task.risk_level in (RiskLevel.L3, RiskLevel.L4, RiskLevel.L5):
                        # Ensure another validator verifies
                        self._independent_verify(task, resp)

                    task.response = resp
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = datetime.now(timezone.utc)
                    completed_ids.add(task.task_id)

                    # Record audit
                    self.audit_service.record_event(AuditEvent(
                        actor=task.assigned_agent,
                        task_id=task.task_id,
                        project=task.project,
                        action=f"task_completed:{task.title}",
                        target=task.title,
                        policy_decision="ALLOW",
                        cost=resp.cost,
                        result=str(resp.result)[:500],
                        verification="INDEPENDENTLY_VERIFIED" if task.risk_level in (RiskLevel.L3, RiskLevel.L4, RiskLevel.L5) else "PASSED"
                    ))
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    task.error = str(e)
                    failed_ids.add(task.task_id)
                    self.audit_service.record_event(AuditEvent(
                        actor=task.assigned_agent,
                        task_id=task.task_id,
                        project=task.project,
                        action=f"task_failed:{task.title}",
                        target=task.title,
                        policy_decision="ALLOW",
                        error=str(e),
                        verification="FAILED"
                    ))

            # If all ready tasks were paused for approval or no tasks could execute, halt loop deterministically
            if not progress_made:
                break

        return self.tasks

    def _independent_verify(self, task: TaskNode, response: AgentResponseContract):
        """Implements H05: A component must not be the sole validator of its own consequential work."""
        # Do not invent a checker identity or evidence simply because the result is non-empty.
        # A separate verifier must supply its own receipt (external to the maker's response).
        raise TaskExecutionBlockedError(
            f"Consequential task {task.task_id} has no independent checker execution receipt; "
            "automated certification is unavailable"
        )
