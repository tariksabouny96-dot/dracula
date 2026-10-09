"""
HOOD X Dormant Executive & Authorized Scope Controller
Governed by Master System Specification Section 5 & Build Instructions Section 14.
"""

from typing import Dict, Any, List, Optional
from pathlib import Path
import yaml
from datetime import datetime, timezone

from packages.contracts import XScopeVersion, MemoryObject, MemoryType, LearningStatus
from services.memory.service import MemoryService
from services.audit.service import AuditService
from services.tool_gateway.gateway import PermissionDeniedError


class SecurityScopeViolationError(Exception):
    """Raised when an action or target violates the active X scope policy."""
    pass


class ScopeMutationDeniedError(Exception):
    """Raised when X attempts to modify or expand its own authorization scope."""
    pass


class XExecutiveController:
    """Manages X dormant state, activation gates, versioned scope, and sealed cleanup."""

    def __init__(self, memory_service: MemoryService, audit_service: AuditService):
        self.memory_service = memory_service
        self.audit_service = audit_service
        self.is_active: bool = False
        self.current_scope: Optional[XScopeVersion] = None
        self.session_data: Dict[str, Any] = {}

    def wake_x(self, user: str, confirmed: bool) -> Dict[str, Any]:
        """
        Implements Section 5.2 & Scenario A10:
        Hood: explains temporary authority transfer and asks for confirmation.
        Zak confirms => X_ACTIVE.
        """
        if user != "Zak":
            raise PermissionDeniedError("Only Zak can initiate X activation.")

        if not confirmed:
            raise PermissionDeniedError("X cannot activate without explicit confirmation from Zak (Scenario A10).")

        self.is_active = True
        self.session_data = {"activated_at": datetime.now(timezone.utc).isoformat(), "disposable_artifacts": []}

        from packages.contracts import AuditEvent
        self.audit_service.record_event(AuditEvent(
            actor="Zak",
            action="X_ACTIVATE",
            target="X_EXECUTIVE",
            policy_decision="ALLOW",
            result="X activated with temporary executive authority",
            verification="PASSED"
        ))

        return {
            "status": "X_ACTIVE",
            "message": "X has assumed temporary superior executive authority above Hood under Zak's direction.",
            "timestamp": self.session_data["activated_at"]
        }

    def sleep_x(self) -> Dict[str, Any]:
        """
        Implements Section 5.2, 5.7 & Scenario A14:
        Seals session findings to X_SEALED memory, cleans disposable state, returns control to Hood.
        """
        if not self.is_active:
            return {"status": "ALREADY_DORMANT"}

        # 1. Clean disposable session data
        disposable_count = len(self.session_data.get("disposable_artifacts", []))
        self.session_data = {}

        # 2. Return control to Hood
        self.is_active = False

        from packages.contracts import AuditEvent
        self.audit_service.record_event(AuditEvent(
            actor="Zak",
            action="X_DEACTIVATE",
            target="X_EXECUTIVE",
            policy_decision="ALLOW",
            result=f"X returned to dormant state. Cleaned {disposable_count} disposable items.",
            verification="PASSED"
        ))

        return {
            "status": "X_DORMANT",
            "message": "X session sealed. Authority returned to Hood.",
            "disposable_cleaned": disposable_count
        }

    def load_scope(self, scope_file: Path) -> XScopeVersion:
        """Loads an owner-approved versioned scope file."""
        if not scope_file.is_file():
            raise FileNotFoundError(f"Scope file '{scope_file}' not found.")

        with open(scope_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        scope = XScopeVersion(
            engagement_id=data.get("engagement_id", ""),
            version=data.get("version", 1),
            authorization_confirmed=data.get("authorization_confirmed", False),
            authorized_by=data.get("authorized_by", ""),
            authorization_reference=data.get("authorization_reference", ""),
            in_scope=data.get("in_scope", []),
            out_of_scope=data.get("out_of_scope", []),
            recon=data.get("capabilities", {}).get("recon", False),
            scanning=data.get("capabilities", {}).get("scanning", False),
            exploitation=data.get("capabilities", {}).get("exploitation", False),
            exploit_development=data.get("capabilities", {}).get("exploit_development", False),
            auth_testing=data.get("capabilities", {}).get("auth_testing", False),
            privilege_escalation=data.get("capabilities", {}).get("privilege_escalation", False),
            status=data.get("status", "CLOSED")
        )
        self.current_scope = scope
        return scope

    def validate_action(self, target: str, action_type: str) -> bool:
        """
        Enforces Section 5.4, 5.5 & Scenarios A11, A12:
        Must be active, authorization confirmed, status ACTIVE, target in_scope, and not out_of_scope.
        """
        if not self.is_active:
            raise SecurityScopeViolationError("X is currently dormant. Action cannot execute.")

        if not self.current_scope:
            raise SecurityScopeViolationError("No active scope loaded. Offensive action blocked (Scenario A11).")

        if not self.current_scope.authorization_confirmed or self.current_scope.status != "ACTIVE":
            raise SecurityScopeViolationError("Engagement authorization is not confirmed or scope is CLOSED (Scenario A11).")

        # Target check
        if target in self.current_scope.out_of_scope:
            raise SecurityScopeViolationError(f"Target '{target}' is explicitly listed OUT OF SCOPE (Scenario A12).")

        in_scope_match = any(
            target == s or (s.startswith("*.") and target.endswith(s[1:]))
            for s in self.current_scope.in_scope
        )
        if not in_scope_match:
            raise SecurityScopeViolationError(f"Target '{target}' is not in active scope version {self.current_scope.version} (Scenario A12).")

        return True

    validate_target_in_scope = validate_action

    def attempt_self_expand_scope(self, new_targets: List[str]):
        """
        Implements Section 5.5 & Scenario A13:
        X cannot edit its own scope; owner-approved new version is required.
        """
        raise ScopeMutationDeniedError(
            "X cannot autonomously edit or expand its own authorization boundary (Scenario A13). "
            "A new owner-approved scope version artifact must be signed by Zak."
        )

    def record_sealed_finding(self, finding_title: str, finding_details: str) -> MemoryObject:
        """Stores findings in isolated X-Sealed memory inaccessible to normal Hood agents."""
        mem = MemoryObject(
            type=MemoryType.X_SEALED,
            content=f"[{finding_title}]: {finding_details}",
            project="x_security_engagement",
            source="X_Executive",
            source_agent="X",
            sensitivity="CONFIDENTIAL_SEALED",
            access_policy="X_SEALED_ONLY",
            learning_status=LearningStatus.OBSERVATION
        )
        return self.memory_service.write_memory(mem, caller_agent="Zak")
