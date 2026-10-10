"""
HOOD Tool Gateway - Base Tool & Capability Enforcer
Governed by Master System Specification Sections 9, 12, 15 & Build Instructions Section 11.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import subprocess
import json
import hashlib
import threading

from packages.contracts import CapabilityGrant, RiskLevel
from packages.config import SystemConfig
from services.policy.governance import RiskEvaluator
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from packages.security import confine_path, PathConfinementError, StopLatch, EmergencyStopActive


class PermissionDeniedError(Exception):
    """Raised when an action violates capability tokens or workspace boundaries."""
    pass


class BaseTool(ABC):
    def __init__(self, name: str, required_capability: str, description: str):
        self.name = name
        self.required_capability = required_capability
        self.description = description

    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> Any:
        pass


class ToolGateway:
    """Enforces capability grants, risk gates, and filesystem boundaries before tool execution."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        approval_service: Optional[ApprovalService] = None,
        audit_service: Optional[AuditService] = None,
        workspace_root: Optional[Path] = None,
        stop_latch: Optional[StopLatch] = None
    ):
        self.config = config or SystemConfig()
        self.approval_service = approval_service or ApprovalService()
        self.audit_service = audit_service or AuditService()
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.tools: Dict[str, BaseTool] = {}
        self.active_grants: Dict[str, CapabilityGrant] = {}
        self._consumed_approvals: set[str] = set()
        self._approval_lock = threading.Lock()
        # Shared with the EmergencyStopController; engaged => no tool runs, no grants issue.
        self.stop_latch = stop_latch or StopLatch()

    def register_tool(self, tool: BaseTool):
        self.tools[tool.name] = tool

    def issue_capability_grant(
        self,
        capability: str,
        target_scope: str,
        granted_to: str,
        ttl_seconds: int = 300
    ) -> CapabilityGrant:
        self.stop_latch.check()
        grant = CapabilityGrant(
            capability=capability,
            target_scope=target_scope,
            granted_to=granted_to,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        )
        self.active_grants[grant.grant_id] = grant
        return grant

    def revoke_all_grants(self) -> int:
        """Used during emergency stop or session reset. Returns the number revoked."""
        revoked = 0
        for grant in list(self.active_grants.values()):
            if not grant.is_revoked:
                grant.is_revoked = True
                revoked += 1
        return revoked

    def halt(self, reason: str) -> int:
        """Engage the stop latch and revoke every grant (emergency stop)."""
        self.stop_latch.engage(reason)
        return self.revoke_all_grants()

    def validate_path(self, target_path: str) -> Path:
        """Enforces that filesystem operations remain within approved workspace roots."""
        try:
            return confine_path(self.workspace_root, target_path, label="target")
        except PathConfinementError as exc:
            raise PermissionDeniedError(f"Path traversal blocked: {exc}") from None

    def _deny(self, caller_agent: str, task_id: Optional[str], tool_name: str, target: str,
              approval_id: Optional[str], reason: str):
        """Every refusal at the execution boundary leaves an audit record."""
        from packages.contracts import AuditEvent
        try:
            self.audit_service.record_event(AuditEvent(
                actor=caller_agent, task_id=task_id, action=f"tool:{tool_name}", target=target,
                policy_decision="DENY", approval_ref=approval_id, tool_or_model=tool_name,
                result="DENIED", error=reason[:500], verification="NOT_EXECUTED"))
        except Exception:
            pass
        raise PermissionDeniedError(reason)

    def invoke_tool(
        self,
        tool_name: str,
        params: Dict[str, Any],
        caller_agent: str = "Hood",
        task_id: Optional[str] = None,
        approval_id: Optional[str] = None
    ) -> Any:
        tool = self.tools.get(tool_name)
        if not tool:
            raise KeyError(f"Tool '{tool_name}' not registered in gateway.")

        target = str(params.get("path") or params.get("target") or tool_name)
        if self.stop_latch.engaged:
            self._deny(caller_agent, task_id, tool_name, target, approval_id,
                       f"Emergency stop is active: {self.stop_latch.reason}")
        risk = RiskEvaluator.assess_risk(tool_name, target, context=params, config=self.config)

        # Privileged tools must carry a live, actor-bound, scoped capability grant.
        # Read-only legacy actions remain available through the non-privileged gateway.
        protected = risk in (RiskLevel.L2, RiskLevel.L3, RiskLevel.L4, RiskLevel.L5) or tool_name == "shell_exec"
        if protected:
            matching_grants = [grant for grant in self.active_grants.values()
                if grant.is_valid() and grant.capability == tool.required_capability
                and grant.granted_to == caller_agent
                and grant.target_scope in (target, "workspace_root" if tool_name != "shell_exec" else "__never__")]
            if not matching_grants:
                self._deny(caller_agent, task_id, tool_name, target, approval_id,
                           f"Capability '{tool.required_capability}' for '{caller_agent}' and '{target}' is not granted")

        # Approval check: approvals must be tied to this tool, action, and task.
        if protected or RiskEvaluator.requires_approval(risk, self.config):
            if not approval_id:
                self._deny(caller_agent, task_id, tool_name, target, approval_id,
                    f"Action '{tool_name}' on '{target}' is Risk Level {risk.value} and requires explicit approval. "
                    "Submit an approval request first.")
            approval = self.approval_service.get_request(approval_id)
            if not approval or not self.approval_service.is_approved(approval_id):
                self._deny(caller_agent, task_id, tool_name, target, approval_id,
                           f"Approval '{approval_id}' has not been approved or has expired")
            if approval.action_type != tool_name or approval.target != target or (task_id and approval.task_id != task_id):
                self._deny(caller_agent, task_id, tool_name, target, approval_id,
                           "Approval is not bound to this exact tool, target and task")
            if approval.principal and approval.principal != caller_agent:
                self._deny(caller_agent, task_id, tool_name, target, approval_id,
                           "Approval was issued for a different principal")
            # An approval for a path is NOT an approval for arbitrary bytes/actions at that path.
            # Require the exact serialized parameters in a user-approved request option.
            param_hash = hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
            if not any(option.get("parameter_sha256") == param_hash for option in approval.options if isinstance(option, dict)):
                self._deny(caller_agent, task_id, tool_name, target, approval_id,
                           "Approval does not contain the required exact parameter hash")
            # Atomically consume one-time approval before any tool side effect.
            with self._approval_lock:
                if approval_id in self._consumed_approvals:
                    self._deny(caller_agent, task_id, tool_name, target, approval_id,
                               "Approval has already been used")
                self._consumed_approvals.add(approval_id)

        # Execute tool
        try:
            result = tool.execute(params)
            # Record audit trace
            from packages.contracts import AuditEvent
            self.audit_service.record_event(AuditEvent(
                actor=caller_agent,
                task_id=task_id,
                action=f"tool:{tool_name}",
                target=target,
                policy_decision="ALLOW",
                approval_ref=approval_id,
                tool_or_model=tool_name,
                result=str(result)[:500],
                verification="UNVERIFIED" if not isinstance(result, dict) or result.get("exit_code", 0) != 0 or result.get("success") is False or result.get("error") else "TOOL_RETURNED"
            ))
            return result
        except Exception as e:
            from packages.contracts import AuditEvent
            self.audit_service.record_event(AuditEvent(
                actor=caller_agent,
                task_id=task_id,
                action=f"tool:{tool_name}",
                target=target,
                policy_decision="ALLOW",
                approval_ref=approval_id,
                tool_or_model=tool_name,
                result="ERROR",
                error=str(e),
                verification="FAILED"
            ))
            raise
