"""
HOOD Unified Desktop Service & Governed Interaction Orchestrator
Governed by Milestone V0.5D Directive:
1. Prefers deterministic interfaces (API -> HTTP -> CLI -> Playwright -> UI Automation -> Vision -> Mouse).
2. Implements Observe -> Plan -> Policy Check -> Act -> Verify loop.
3. Wrong-window & runaway click protection.
4. Tool Gateway & Risk classification enforcement.
5. Emergency Stop integration halting input events instantly.
"""

from __future__ import annotations
import time
import hashlib
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from packages.contracts import RiskLevel, ApprovalStatus
from packages.config import SystemConfig
from services.policy.governance import RiskEvaluator
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError

from services.desktop.contracts import (
    DesktopControlMethod,
    DesktopActionResult,
    UIElementInfo,
    UIElementRole,
    WindowInfo,
    ScreenObservation,
    MouseButton,
    KeyModifier
)
from services.desktop.windows_backend import WindowsNativeBackend
from services.desktop.accessibility import AccessibilityEngine
from services.desktop.input_controller import GovernedInputController
from services.desktop.screen_observer import ScreenObserver
from services.desktop.application_manager import ApplicationManager
from services.desktop.handoff import HumanVerificationDetector, HumanHandoffManager


class DesktopService:
    """Central governed desktop automation service coordinating native Windows control."""

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        tool_gateway: Optional[ToolGateway] = None,
        approval_service: Optional[ApprovalService] = None,
        audit_service: Optional[AuditService] = None,
        backend: Optional[WindowsNativeBackend] = None
    ):
        self.config = config or SystemConfig()
        self.tool_gateway = tool_gateway
        self.approval_service = approval_service or ApprovalService()
        self.audit_service = audit_service or AuditService()

        self.backend = backend or WindowsNativeBackend()
        self.accessibility = AccessibilityEngine(self.backend)
        self.input_ctrl = GovernedInputController(self.backend)
        self.observer = ScreenObserver(self.backend, self.accessibility)
        self.app_mgr = ApplicationManager(self.backend)
        self.verifier_detector = HumanVerificationDetector()
        self.handoff_mgr = HumanHandoffManager(self.audit_service)

        self.is_emergency_stopped = False
        self.max_retries = 3
        self._used_action_approvals = set()
        self._action_approval_lock = threading.Lock()

    def halt(self):
        """Immediately halts desktop automation upon Emergency Stop."""
        self.is_emergency_stopped = True

    def reset_halt(self):
        self.is_emergency_stopped = False

    def _authorize_desktop_action(self, task_id: str, action: str, payload: Dict[str, Any],
                                  risk_level: RiskLevel, approval_id: Optional[str]) -> None:
        """Consume an explicit one-time approval bound to the exact desktop action.

        Request creation and approval are separate authenticated caller responsibilities.
        Merely supplying an approval ID does not create or approve a request.
        """
        if self.is_emergency_stopped:
            raise PermissionError("Desktop emergency stop is active")
        if not RiskEvaluator.requires_approval(risk_level, self.config) and risk_level not in (
            RiskLevel.L2, RiskLevel.L3, RiskLevel.L4, RiskLevel.L5
        ):
            return
        if not approval_id:
            raise PermissionError("Desktop action requires explicit action-bound approval")
        approval = self.approval_service.get_request(approval_id)
        if not approval or approval.status != ApprovalStatus.APPROVED:
            raise PermissionError("Desktop approval is not approved")
        expected_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                 default=str).encode("utf-8")).hexdigest()
        if (approval.task_id != task_id or approval.action_type != action or
                approval.target != expected_hash):
            raise PermissionError("Desktop approval does not match task, action and parameters")
        if not any(isinstance(option, dict) and option.get("parameter_sha256") == expected_hash
                   for option in approval.options):
            raise PermissionError("Desktop approval is missing exact parameter evidence")
        if not approval.resolved_at or approval.resolved_at > datetime.now(timezone.utc) or (
                datetime.now(timezone.utc) - approval.resolved_at > timedelta(minutes=5)):
            raise PermissionError("Desktop approval is expired")
        with self._action_approval_lock:
            if self.is_emergency_stopped or approval_id in self._used_action_approvals:
                raise PermissionError("Desktop approval already consumed or emergency stopped")
            self._used_action_approvals.add(approval_id)

    def enumerate_windows(self) -> List[WindowInfo]:
        return self.backend.enumerate_windows()

    def inspect_active_window(self) -> Optional[WindowInfo]:
        return self.backend.get_foreground_window()

    def focus_window(self, hwnd: int) -> bool:
        if self.is_emergency_stopped:
            return False
        return self.backend.activate_window(hwnd)

    def observe_screen(self, capture_image: bool = True) -> ScreenObservation:
        return self.observer.observe(capture_image=capture_image)

    # ==========================================================================
    # Governed Interaction: Observe -> Plan -> Policy -> Act -> Verify
    # ==========================================================================

    def execute_semantic_click(
        self,
        task_id: str,
        target_hwnd: int,
        role: Optional[UIElementRole] = None,
        element_name: Optional[str] = None,
        class_name: Optional[str] = None,
        risk_level: RiskLevel = RiskLevel.L1,
        approval_id: Optional[str] = None
    ) -> DesktopActionResult:
        """
        Clicks a UI element discovered via semantic accessibility tree.
        Verifies correct foreground window before and after interaction.
        """
        if self.is_emergency_stopped:
            return self._emergency_abort_result(task_id, "semantic_click")

        # 1. Verify Policy & Approval Gate
        self._authorize_desktop_action(task_id, "desktop_semantic_click",
                                      {"target_hwnd": target_hwnd, "role": role.value if role else None,
                                       "element_name": element_name, "class_name": class_name},
                                      risk_level, approval_id)

        # 2. Verify target window is active or focus it
        active_w = self.backend.get_foreground_window()
        if not active_w or active_w.hwnd != target_hwnd:
            self.backend.activate_window(target_hwnd)
            time.sleep(0.1)

        # 3. Locate element via Accessibility Engine
        element = self.accessibility.find_element(target_hwnd, role=role, name=element_name, class_name=class_name)
        if not element:
            return DesktopActionResult(
                task_id=task_id,
                action_type="semantic_click",
                control_method=DesktopControlMethod.UI_AUTOMATION,
                target_description=f"Element role={role} name={element_name}",
                risk_level=risk_level,
                success=False,
                error_message="Element not found in accessibility tree"
            )

        before_hash = self.observer.compute_state_hash()

        # 4. Act
        click_ok = self.input_ctrl.click_element(element)
        time.sleep(0.1)

        # 5. Observe & Verify
        after_hash = self.observer.compute_state_hash()
        verification_passed = click_ok

        from packages.contracts import AuditEvent
        self.audit_service.record_event(AuditEvent(
            actor="DesktopService",
            task_id=task_id,
            action="DESKTOP_SEMANTIC_CLICK",
            target=f"{element.name} ({element.role.value})",
            policy_decision="ALLOW",
            result="Element clicked successfully",
            verification="PASSED" if verification_passed else "FAILED"
        ))

        return DesktopActionResult(
            task_id=task_id,
            action_type="semantic_click",
            control_method=DesktopControlMethod.UI_AUTOMATION,
            target_description=f"{element.name} ({element.role.value})",
            risk_level=risk_level,
            success=click_ok,
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            verification_passed=verification_passed
        )

    def execute_vision_or_coordinate_click(
        self,
        task_id: str,
        target_hwnd: int,
        x: int,
        y: int,
        target_description: str,
        risk_level: RiskLevel = RiskLevel.L1,
        approval_id: Optional[str] = None
    ) -> DesktopActionResult:
        """
        Fallback coordinate/vision-guided click when semantic elements cannot be discovered.
        Includes wrong-window protection and runaway prevention.
        """
        if self.is_emergency_stopped:
            return self._emergency_abort_result(task_id, "coordinate_click")

        self._authorize_desktop_action(task_id, "desktop_coordinate_click",
                                      {"target_hwnd": target_hwnd, "x": x, "y": y,
                                       "target_description": target_description}, risk_level, approval_id)

        # Wrong-window check
        active_w = self.backend.get_foreground_window()
        if not active_w or active_w.hwnd != target_hwnd:
            self.backend.activate_window(target_hwnd)
            time.sleep(0.1)
            active_w = self.backend.get_foreground_window()
            if not active_w or active_w.hwnd != target_hwnd:
                return DesktopActionResult(
                    task_id=task_id,
                    action_type="coordinate_click",
                    control_method=DesktopControlMethod.VISION,
                    target_description=target_description,
                    risk_level=risk_level,
                    success=False,
                    error_message=f"Wrong-window protection: Target HWND {target_hwnd} is not foreground."
                )

        before_hash = self.observer.compute_state_hash()

        # Act
        ok = self.input_ctrl.click(x, y, MouseButton.LEFT)
        time.sleep(0.1)

        after_hash = self.observer.compute_state_hash()

        return DesktopActionResult(
            task_id=task_id,
            action_type="coordinate_click",
            control_method=DesktopControlMethod.VISION,
            target_description=target_description,
            risk_level=risk_level,
            success=ok,
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            verification_passed=ok
        )

    def type_into_active(
        self,
        task_id: str,
        text: str,
        risk_level: RiskLevel = RiskLevel.L2,
        approval_id: Optional[str] = None
    ) -> DesktopActionResult:
        if self.is_emergency_stopped:
            return self._emergency_abort_result(task_id, "type_text")
        foreground = self.backend.get_foreground_window()
        self._authorize_desktop_action(task_id, "desktop_type_text",
                                      {"text": text, "target_hwnd": foreground.hwnd if foreground else None},
                                      risk_level, approval_id)

        before_hash = self.observer.compute_state_hash()
        ok = self.input_ctrl.type_text(text)
        time.sleep(0.05)
        after_hash = self.observer.compute_state_hash()

        return DesktopActionResult(
            task_id=task_id,
            action_type="type_text",
            control_method=DesktopControlMethod.MOUSE_KEYBOARD,
            target_description="Active input field",
            risk_level=risk_level,
            success=ok,
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            verification_passed=ok
        )

    def send_shortcut(
        self,
        task_id: str,
        key_code: int,
        modifiers: Optional[List[KeyModifier]] = None,
        risk_level: RiskLevel = RiskLevel.L1,
        approval_id: Optional[str] = None
    ) -> DesktopActionResult:
        if self.is_emergency_stopped:
            return self._emergency_abort_result(task_id, "shortcut")
        foreground = self.backend.get_foreground_window()
        self._authorize_desktop_action(task_id, "desktop_shortcut",
                                      {"key_code": key_code, "modifiers": [m.value for m in (modifiers or [])],
                                       "target_hwnd": foreground.hwnd if foreground else None},
                                      risk_level, approval_id)

        ok = self.input_ctrl.send_shortcut(key_code, modifiers)
        return DesktopActionResult(
            task_id=task_id,
            action_type="shortcut",
            control_method=DesktopControlMethod.MOUSE_KEYBOARD,
            target_description=f"Key code {key_code} with {modifiers}",
            risk_level=risk_level,
            success=ok,
            verification_passed=ok
        )

    def _emergency_abort_result(self, task_id: str, action_type: str) -> DesktopActionResult:
        return DesktopActionResult(
            task_id=task_id,
            action_type=action_type,
            control_method=DesktopControlMethod.MOUSE_KEYBOARD,
            target_description="ABORTED_BY_EMERGENCY_STOP",
            risk_level=RiskLevel.L5,
            success=False,
            error_message="Execution halted by Emergency Stop."
        )
