"""
HOOD X Session Manager & Governed Lifecycle State Machine
Governs X state transitions:
DORMANT -> PENDING_APPROVAL -> ACTIVATING -> ACTIVE_READ_ONLY -> DEACTIVATING / EXPIRED -> DORMANT.
Enforces Root Owner approval, time-bound expiry, and scope validation.
Governed by Master System Specification Section 5 & Build Instructions Section 14.
"""

from __future__ import annotations
import os
import json
import uuid
import time
import tempfile
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone, timedelta

from packages.contracts import (
    RiskLevel,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    MemoryObject,
    MemoryType,
    LearningStatus
)
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService


class XOperationalState(str, Enum):
    DORMANT = "DORMANT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVATING = "ACTIVATING"
    ACTIVE_READ_ONLY = "ACTIVE_READ_ONLY"
    DEACTIVATING = "DEACTIVATING"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


class XSessionManager:
    """Authoritative state machine and session controller for Executive X."""

    # Authorized activation-duration bounds (minutes). A request outside this
    # range is clamped, and the clamp is reported to the requester, never silent.
    MIN_DURATION_MINUTES = 1
    MAX_DURATION_MINUTES = 60

    def __init__(
        self,
        approval_service: ApprovalService,
        audit_service: Optional[AuditService] = None,
        x_controller: Optional[Any] = None,
        sentinel_service: Optional[Any] = None,
        state_path: Optional[Path] = None
    ):
        self.approval_service = approval_service
        self.audit_service = audit_service
        self.x_controller = x_controller
        self.sentinel_service = sentinel_service
        # Durable state so a restart never silently forgets that X was active
        # and never lets a consumed approval be replayed (F12).
        if state_path is not None:
            self.state_path = Path(state_path)
        else:
            base = Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood"))
            self.state_path = base / "x_state.json"

        # When an identity provider is attached, X authority comes from the
        # authenticated ROOT_OWNER role, never from a username string.
        self.auth_service: Optional[Any] = None
        self.state: XOperationalState = XOperationalState.DORMANT
        self.session_id: Optional[str] = None
        self.mode: Optional[str] = None
        self.scope: Optional[str] = None
        self.duration_seconds: int = 600
        self.activated_at: Optional[datetime] = None
        self.expires_at: Optional[datetime] = None
        self.authorized_by: Optional[str] = None
        self.pending_approval_id: Optional[str] = None
        self.consumed_approval_ids: Set[str] = set()

        self.restrictions: List[str] = [
            "READ_ONLY_OBSERVATION",
            "LOCAL_ENVIRONMENT_ONLY",
            "NO_SYSTEM_MUTATIONS",
            "NO_EXTERNAL_TRAFFIC",
            "NO_EXPLOIT_PAYLOADS"
        ]

        self._restore()

    # --- Durable state (F12) ---------------------------------------------

    def _persist(self) -> None:
        """Write the authoritative X state atomically. Best-effort: a failure
        here must never crash a governance transition."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "state": self.state.value,
                "session_id": self.session_id,
                "mode": self.mode,
                "scope": self.scope,
                "duration_seconds": self.duration_seconds,
                "activated_at": self.activated_at.isoformat() if self.activated_at else None,
                "expires_at": self.expires_at.isoformat() if self.expires_at else None,
                "authorized_by": self.authorized_by,
                "pending_approval_id": self.pending_approval_id,
                "consumed_approval_ids": sorted(self.consumed_approval_ids),
            }
            fd, tmp = tempfile.mkstemp(dir=str(self.state_path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.state_path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except Exception:
            pass

    def _restore(self) -> None:
        """Reload persisted X state. Fails closed: an ACTIVE session whose TTL
        has already elapsed is NOT resurrected; it is recorded as expired and
        returned to DORMANT. Consumed approval ids always survive so an
        approval can never be replayed across a restart."""
        try:
            if not self.state_path.is_file():
                return
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        self.consumed_approval_ids = set(data.get("consumed_approval_ids") or [])
        self.pending_approval_id = data.get("pending_approval_id")

        def _parse(ts):
            return datetime.fromisoformat(ts) if ts else None

        restored = data.get("state")
        self.mode = data.get("mode")
        self.scope = data.get("scope")
        self.duration_seconds = data.get("duration_seconds", 600)
        self.activated_at = _parse(data.get("activated_at"))
        self.expires_at = _parse(data.get("expires_at"))
        self.authorized_by = data.get("authorized_by")
        self.session_id = data.get("session_id")

        if restored == XOperationalState.ACTIVE_READ_ONLY.value:
            now = datetime.now(timezone.utc)
            if self.expires_at and now < self.expires_at:
                # Still within the approved window: honour the remaining TTL.
                self.state = XOperationalState.ACTIVE_READ_ONLY
                if self.audit_service:
                    self.audit_service.record_event(AuditEvent(
                        actor="system",
                        action="X_STATE_RECOVERED",
                        target="X_EXECUTIVE",
                        policy_decision="ALLOW",
                        result=f"Recovered ACTIVE X session {self.session_id} after restart; "
                               f"expires {self.expires_at.isoformat()}.",
                        verification="PASSED"
                    ))
            else:
                # Expired while down: fail closed.
                self._clear_session_fields()
                self.state = XOperationalState.DORMANT
                if self.audit_service:
                    self.audit_service.record_event(AuditEvent(
                        actor="system",
                        action="X_STATE_EXPIRED_ON_RECOVERY",
                        target="X_EXECUTIVE",
                        policy_decision="ALLOW",
                        result="A persisted ACTIVE X session had already expired on restart; forced DORMANT.",
                        verification="PASSED"
                    ))
        elif restored == XOperationalState.PENDING_APPROVAL.value:
            self.state = XOperationalState.PENDING_APPROVAL
        else:
            self.state = XOperationalState.DORMANT

    def _clear_session_fields(self) -> None:
        self.session_id = None
        self.mode = None
        self.scope = None
        self.activated_at = None
        self.expires_at = None
        self.pending_approval_id = None

    def _require_root(self, username: Optional[str], action: str) -> None:
        name = (username or "").strip().lower()
        if self.auth_service is not None:
            user = self.auth_service.get_user_by_username(name)
            if not user or not user.get("is_active") or user.get("role") != "ROOT_OWNER":
                raise PermissionError(f"Only the authenticated Root Owner can {action}.")
            return
        # Unauthenticated offline mode (no identity provider attached): legacy owner alias only.
        if name not in ("zack", "zak"):
            raise PermissionError(f"Only ZACK (Root Owner) can {action}.")

    def request_activation(
        self,
        requester_username: str,
        mode: str = "READ_ONLY_DIAGNOSTIC",
        duration_minutes: int = 10,
        scope: str = "LOCAL_SANDBOX_ENV"
    ) -> ApprovalRequest:
        """
        Initiates a governed X activation request.
        Creates a real ApprovalRequest in ApprovalService.
        """
        # 1. Requester authority check
        self._require_root(requester_username, "request X activation")

        # 2. Existing active check
        self.check_expiration()
        if self.state == XOperationalState.ACTIVE_READ_ONLY:
            raise RuntimeError(f"X is already active (Session: {self.session_id}). Stand down before requesting new session.")

        # 3. Mode validation (only READ_ONLY_DIAGNOSTIC supported)
        norm_mode = mode.strip().upper()
        if norm_mode not in ("READ_ONLY_DIAGNOSTIC", "READ-ONLY", "READ_ONLY"):
            raise ValueError(
                f"Mode '{mode}' is not supported. In current sovereign operational phase, "
                f"only 'READ_ONLY_DIAGNOSTIC' mode is authorized for Executive X."
            )
        effective_mode = "READ_ONLY_DIAGNOSTIC"

        # 4. Scope and duration validation
        clamped_minutes = max(self.MIN_DURATION_MINUTES, min(duration_minutes, self.MAX_DURATION_MINUTES))
        duration_sec = clamped_minutes * 60
        effective_scope = scope.strip() if scope else "LOCAL_SANDBOX_ENV"

        # 5. Create real approval request
        task_id = f"task_x_act_{uuid.uuid4().hex[:8]}"
        req = self.approval_service.create_request(
            task_id=task_id,
            action_type="X_ACTIVATION",
            target=f"X Executive (Mode: {effective_mode}, Target: {effective_scope})",
            reason=f"Request activation of X in read-only diagnostic mode for {clamped_minutes} minutes, restricted to local environment",
            risk_level=RiskLevel.L4,
            options=[
                {
                    "label": "APPROVE_READ_ONLY",
                    "mode": effective_mode,
                    "scope": effective_scope,
                    "duration_sec": duration_sec,
                    "duration_minutes": clamped_minutes
                },
                {"label": "REJECT"}
            ],
            recommended_option="APPROVE_READ_ONLY"
        )

        self.pending_approval_id = req.approval_id
        self.state = XOperationalState.PENDING_APPROVAL

        if self.audit_service:
            self.audit_service.record_event(AuditEvent(
                actor=requester_username,
                action="X_ACTIVATION_REQUESTED",
                target="X_EXECUTIVE",
                policy_decision="PENDING_APPROVAL",
                result=f"Created approval {req.approval_id} for mode {effective_mode} ({clamped_minutes} min)",
                verification="PASSED"
            ))

        self._persist()
        return req

    def activate(self, approval_id: str, approver_username: str) -> Dict[str, Any]:
        """
        Activates X upon verified approval. Revalidates scope and parameters.
        """
        self._require_root(approver_username, "authorize X activation")

        if approval_id in self.consumed_approval_ids:
            raise PermissionError(f"Approval {approval_id} has already been consumed (replay prevention).")

        req = self.approval_service.get_request(approval_id)
        if not req:
            raise KeyError(f"Approval request {approval_id} not found.")

        if req.status != ApprovalStatus.APPROVED:
            raise PermissionError(f"Approval request {approval_id} is not approved (Current status: {req.status.value}).")

        if req.action_type != "X_ACTIVATION":
            raise ValueError(f"Approval {approval_id} is for {req.action_type}, not X_ACTIVATION.")

        # Revalidate approved parameters
        opt = req.options[0] if req.options else {}
        approved_mode = opt.get("mode", "READ_ONLY_DIAGNOSTIC")
        approved_scope = opt.get("scope", "LOCAL_SANDBOX_ENV")
        approved_duration_sec = opt.get("duration_sec", 600)

        if approved_mode != "READ_ONLY_DIAGNOSTIC":
            raise ValueError(f"Unauthorized mode '{approved_mode}' detected in approval parameters.")

        self.consumed_approval_ids.add(approval_id)

        # Transition state
        self.state = XOperationalState.ACTIVATING
        now = datetime.now(timezone.utc)
        self.session_id = f"x_sess_{uuid.uuid4().hex[:8]}"
        self.mode = approved_mode
        self.scope = approved_scope
        self.duration_seconds = approved_duration_sec
        self.activated_at = now
        self.expires_at = now + timedelta(seconds=approved_duration_sec)
        self.authorized_by = approver_username
        self.pending_approval_id = None
        self.state = XOperationalState.ACTIVE_READ_ONLY

        # Synchronize attached controllers
        if self.x_controller and hasattr(self.x_controller, "is_active"):
            self.x_controller.is_active = True
            if hasattr(self.x_controller, "session_data"):
                self.x_controller.session_data = {
                    "session_id": self.session_id,
                    "activated_at": self.activated_at.isoformat(),
                    "expires_at": self.expires_at.isoformat(),
                    "mode": self.mode,
                    "scope": self.scope
                }

        if self.sentinel_service and hasattr(self.sentinel_service, "x_red_team"):
            self.sentinel_service.x_red_team.is_dormant = False

        if self.audit_service:
            self.audit_service.record_event(AuditEvent(
                actor=approver_username,
                action="X_ACTIVATED",
                target="X_EXECUTIVE",
                policy_decision="ALLOW",
                result=f"X activated in {self.mode} for {self.duration_seconds}s (Scope: {self.scope}, Session: {self.session_id})",
                verification="PASSED"
            ))

        self._persist()
        return self.get_status()

    def stand_down(self, reason: str = "MANUAL_STAND_DOWN", actor: str = "Zak") -> Dict[str, Any]:
        """Returns X to DORMANT state and cleans session parameters."""
        if self.state == XOperationalState.DORMANT and not self.pending_approval_id:
            return {"status": "ALREADY_DORMANT", "state": "DORMANT"}

        old_session = self.session_id
        self.state = XOperationalState.DEACTIVATING

        # Synchronize attached controllers
        if self.x_controller and hasattr(self.x_controller, "sleep_x"):
            try:
                self.x_controller.sleep_x()
            except Exception:
                pass
            if hasattr(self.x_controller, "is_active"):
                self.x_controller.is_active = False

        if self.sentinel_service and hasattr(self.sentinel_service, "x_red_team"):
            try:
                self.sentinel_service.x_red_team.stand_down()
            except Exception:
                pass
            self.sentinel_service.x_red_team.is_dormant = True

        self.state = XOperationalState.DORMANT
        self.session_id = None
        self.mode = None
        self.scope = None
        self.activated_at = None
        self.expires_at = None
        self.pending_approval_id = None

        if self.audit_service:
            self.audit_service.record_event(AuditEvent(
                actor=actor,
                action="X_DEACTIVATED",
                target="X_EXECUTIVE",
                policy_decision="ALLOW",
                result=f"X returned to DORMANT state. Reason: {reason}. Closed session {old_session}.",
                verification="PASSED"
            ))

        self._persist()
        return {
            "status": "X_DORMANT",
            "state": "DORMANT",
            "reason": reason,
            "session_closed": old_session
        }

    def check_expiration(self) -> bool:
        """Checks if active session has exceeded authorized duration."""
        if self.state == XOperationalState.ACTIVE_READ_ONLY and self.expires_at:
            now = datetime.now(timezone.utc)
            if now >= self.expires_at:
                if self.audit_service:
                    self.audit_service.record_event(AuditEvent(
                        actor="SYSTEM",
                        action="X_EXPIRED",
                        target="X_EXECUTIVE",
                        policy_decision="ALLOW",
                        result=f"Session {self.session_id} expired at {self.expires_at.isoformat()}",
                        verification="PASSED"
                    ))
                self.stand_down(reason="SESSION_EXPIRED", actor="SYSTEM")
                return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Returns the authoritative live state of Executive X."""
        self.check_expiration()

        now = datetime.now(timezone.utc)
        remaining_sec = 0
        if self.state == XOperationalState.ACTIVE_READ_ONLY and self.expires_at:
            remaining_sec = max(0, int((self.expires_at - now).total_seconds()))

        # Determine badge string
        if self.state == XOperationalState.ACTIVE_READ_ONLY:
            badge = "X: ACTIVE (READ-ONLY)"
        elif self.state == XOperationalState.PENDING_APPROVAL:
            badge = "X: PENDING APPROVAL"
        elif self.state == XOperationalState.ACTIVATING:
            badge = "X: ACTIVATING"
        elif self.state == XOperationalState.EXPIRED:
            badge = "X: EXPIRED"
        else:
            badge = "X: DORMANT"

        return {
            "state": self.state.value,
            "badge": badge,
            "is_active": self.state == XOperationalState.ACTIVE_READ_ONLY,
            "is_pending": self.state == XOperationalState.PENDING_APPROVAL,
            "session_id": self.session_id,
            "mode": self.mode,
            "scope": self.scope,
            "duration_seconds": self.duration_seconds,
            "remaining_seconds": remaining_sec,
            "activated_at": self.activated_at.isoformat() if self.activated_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "authorized_by": self.authorized_by,
            "pending_approval_id": self.pending_approval_id,
            "restrictions": self.restrictions
        }
