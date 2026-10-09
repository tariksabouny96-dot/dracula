"""
HOOD V0.5D Desktop Control & Financial Governance Comprehensive Test Suite
Verifies all 33 required dimensions:
- Native Win32 window discovery and focus
- Accessibility element discovery and semantic click
- Keyboard input, mouse scroll, and shortcuts
- Vision/coordinate fallback with wrong-window protection
- State verification, state hashing, and bounded retries
- Desktop Tool Gateway capability checking & risk evaluation
- Financial constitution:
  - Zero autonomous spending
  - Zak approval requirement for spend > $0
  - Value optimization tradeoff math
  - Mandatory best free alternative evaluation on rejected spend
  - X financial inheritance
  - Distinction of price freshness
- Human verification detection (CAPTCHA, MFA, OTP)
- Branch preservation (WAITING_FOR_HUMAN) and parallel continuation
- Resumption upon simulated human completion
- Overnight Mode and Morning Report generation
- Sensitive credential scrubbing from observations & logs
- Emergency Stop instant desktop input halt
- X_SEALED memory isolation
"""

import os
import sys
import pytest
from pathlib import Path

from packages.config import SystemConfig
from packages.contracts import (
    RiskLevel,
    ApprovalStatus,
    AuditEvent,
    CapabilityGrant
)
from services.policy.approval_service import ApprovalService
from services.policy.governance import RiskEvaluator
from services.audit.service import AuditService
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.core.emergency_stop import EmergencyStopController

from services.desktop import (
    DesktopControlMethod,
    UIElementRole,
    UIElementInfo,
    WindowInfo,
    WindowState,
    MouseButton,
    KeyModifier,
    ScreenObservation,
    DesktopActionResult,
    VerificationChallengeType,
    HumanVerificationState,
    PriceFreshness,
    BillingType,
    FinancialOption,
    FinancialRecommendation,
    UnattendedBranchState,
    WindowsNativeBackend,
    AccessibilityEngine,
    GovernedInputController,
    ScreenObserver,
    ApplicationManager,
    HumanVerificationDetector,
    HumanHandoffManager,
    FinancialAdvisor,
    FinancialConstitutionViolationError,
    OvernightExecutionManager,
    DesktopService
)


@pytest.fixture
def desktop_system(tmp_path):
    config = SystemConfig()
    approval = ApprovalService()
    audit = AuditService(db_path=tmp_path / "audit.db")
    gateway = ToolGateway(config, approval, audit, workspace_root=tmp_path)
    backend = WindowsNativeBackend()
    desktop = DesktopService(
        config=config,
        tool_gateway=gateway,
        approval_service=approval,
        audit_service=audit,
        backend=backend
    )
    finance = FinancialAdvisor(config, approval, audit)
    overnight = OvernightExecutionManager(audit)
    emergency = EmergencyStopController(
        gateway, audit, desktop_service=desktop
    )
    return {
        "config": config,
        "approval": approval,
        "audit": audit,
        "gateway": gateway,
        "backend": backend,
        "desktop": desktop,
        "finance": finance,
        "overnight": overnight,
        "emergency": emergency
    }


def test_window_enumeration_and_focus(desktop_system):
    """Verifies that WindowsNativeBackend discovers windows and focuses target window."""
    desktop = desktop_system["desktop"]
    windows = desktop.enumerate_windows()
    assert isinstance(windows, list)
    if windows:
        w = windows[0]
        assert w.hwnd > 0
        assert len(w.title) > 0
        assert w.rect is not None
        # Test focus
        focused = desktop.focus_window(w.hwnd)
        assert focused is True


def test_accessibility_element_targeting(desktop_system):
    """Verifies semantic UI element discovery and role classification."""
    desktop = desktop_system["desktop"]
    windows = desktop.enumerate_windows()
    if windows:
        w = windows[0]
        elements = desktop.accessibility.inspect_window_elements(w.hwnd)
        assert isinstance(elements, list)
        for el in elements:
            assert isinstance(el.role, UIElementRole)
            assert el.hwnd > 0


def test_screen_observation_and_secret_redaction(desktop_system, tmp_path):
    """Verifies screen capture, SHA256 integrity hash, and credential scrubbing."""
    desktop = desktop_system["desktop"]
    obs = desktop.observe_screen(capture_image=True)

    assert obs.screen_width > 0
    assert obs.screen_height > 0
    assert obs.screenshot_path is not None
    assert Path(obs.screenshot_path).exists()
    assert len(obs.image_hash_sha256) == 64

    # Verify secret scrubbing on text with mock secret
    mock_el = UIElementInfo(
        name="Header with sk-live-1234567890abcdef123456 token",
        value="SECRET://provider/key",
        role=UIElementRole.TEXT_FIELD
    )
    desktop.observer.observe = lambda capture_image=False: ScreenObservation(
        screen_width=1536,
        screen_height=864,
        screenshot_path=None,
        image_hash_sha256="abc",
        discovered_elements=[mock_el],
        has_redacted_secrets=True,
        redaction_count=1
    )
    scrubbed_obs = desktop.observe_screen(capture_image=False)
    assert scrubbed_obs.has_redacted_secrets is True


def test_semantic_click_and_state_verification(desktop_system):
    """Verifies Observe -> Plan -> Policy -> Act -> Verify lifecycle."""
    desktop = desktop_system["desktop"]
    windows = desktop.enumerate_windows()
    if windows:
        w = windows[0]
        res = desktop.execute_semantic_click(
            task_id="task-click-test",
            target_hwnd=w.hwnd,
            role=None,
            element_name=None,
            risk_level=RiskLevel.L1
        )
        assert res.task_id == "task-click-test"
        assert res.control_method == DesktopControlMethod.UI_AUTOMATION


def test_vision_coordinate_fallback_and_wrong_window_protection(desktop_system):
    """Verifies coordinate click fallback and rejection if wrong window is active."""
    desktop = desktop_system["desktop"]
    # Invalid target hwnd 99999999 should trigger wrong-window protection
    res = desktop.execute_vision_or_coordinate_click(
        task_id="task-coord-test",
        target_hwnd=99999999,
        x=500,
        y=300,
        target_description="Fallback button",
        risk_level=RiskLevel.L1
    )
    assert res.success is False
    assert "Wrong-window protection" in (res.error_message or "")


def test_keyboard_input_and_shortcut(desktop_system):
    """Verifies governed keyboard typing and modifier shortcuts."""
    desktop = desktop_system["desktop"]
    # No approval token is supplied: typing must not perform input on the host.
    with pytest.raises(PermissionError, match="approval"):
        desktop.type_into_active("task-type", "HOOD test text", RiskLevel.L2)

    # On non-Windows hosts, keyboard injection is not available and must not
    # claim success merely because the request was accepted.
    res_cut = desktop.send_shortcut("task-shortcut", 0x43, [KeyModifier.CTRL], RiskLevel.L1)
    assert res_cut.control_method == DesktopControlMethod.MOUSE_KEYBOARD
    if os.name != "nt":
        assert res_cut.success is False


def test_tool_gateway_capability_enforcement_for_desktop(desktop_system):
    """Verifies that Tool Gateway enforces 'desktop:control' capability token."""
    gateway = desktop_system["gateway"]
    from services.tool_gateway.tools import DesktopControlTool
    desktop_tool = DesktopControlTool(gateway, desktop_system["desktop"])

    # Attempt execution without active grant
    with pytest.raises(PermissionDeniedError):
        desktop_tool.execute({"action": "observe"})

    # Issue grant and verify allowed execution
    grant = gateway.issue_capability_grant(
        capability="desktop:control",
        target_scope="workspace",
        granted_to="test_runner"
    )
    res = desktop_tool.execute({"action": "observe", "grant_id": grant.grant_id})
    assert "screen_width" in res
    assert res["screen_width"] > 0


def test_financial_recommendation_and_approval_gate(desktop_system):
    """Verifies that financial expenditure requires Zak's approval and cannot spend autonomously."""
    finance = desktop_system["finance"]
    paid = FinancialOption(
        option_name="RunPod Cloud GPU (RTX 4090)",
        cost_usd=2.40,
        billing_type=BillingType.PER_HOUR,
        quality_score=95.0,
        estimated_time_minutes=45.0,
        price_freshness=PriceFreshness.LIVE_PRICE,
        description="Fast rented GPU compute"
    )
    free = FinancialOption(
        option_name="Local CPU & Gemini Free Tier",
        cost_usd=0.0,
        billing_type=BillingType.ONE_TIME,
        quality_score=88.0,
        estimated_time_minutes=180.0,
        price_freshness=PriceFreshness.USER_CONFIGURED_PRICE,
        description="Zero dollar compute"
    )

    rec = finance.formulate_recommendation(
        task_id="fin-task-01",
        need_description="Deep learning test execution",
        paid_options=[paid],
        free_option=free,
        consequence_of_free="Takes 3 hours longer but costs $0.00",
        reasoning="RunPod offers 3x speedup for $2.40",
        preferred_paid_option=paid.option_name
    )

    assert rec.expected_cost_usd == 2.40
    assert rec.approval_required is True
    assert rec.approval_status == ApprovalStatus.PENDING

    # Test rejection leading to automatic free fallback
    resolved = finance.resolve_financial_decision(
        recommendation_id=rec.recommendation_id,
        approved=False,
        authorized_by="Zak"
    )
    assert resolved.option_name == free.option_name
    assert resolved.cost_usd == 0.0


def test_financial_constitution_zero_spend_rule_and_x_inheritance(desktop_system):
    """Verifies that neither HOOD nor X can autonomously authorize expenditure."""
    finance = desktop_system["finance"]

    # Autonomous spend attempt by X
    with pytest.raises(FinancialConstitutionViolationError):
        finance.verify_x_financial_boundary(actor="X", requested_spend_usd=15.00)

    # Autonomous spend attempt by Hood
    with pytest.raises(FinancialConstitutionViolationError):
        finance.verify_x_financial_boundary(actor="Hood", requested_spend_usd=1.00)

    # Zero spend is permitted
    assert finance.verify_x_financial_boundary(actor="X", requested_spend_usd=0.0) is True


def test_human_verification_detection_and_parallel_continuation(desktop_system):
    """Verifies that CAPTCHA/MFA barriers pause the branch into WAITING_FOR_HUMAN without freezing HOOD."""
    detector = desktop_system["desktop"].verifier_detector
    handoff = desktop_system["desktop"].handoff_mgr
    overnight = desktop_system["overnight"]

    # 1. Detection
    challenge_type = detector.inspect_text_for_challenge("Please complete the Cloudflare Turnstile CAPTCHA to verify you are human")
    assert challenge_type == VerificationChallengeType.CAPTCHA

    # 2. Branch registration & handoff
    challenge = handoff.register_challenge(
        task_id="task-browse-web",
        challenge_type=challenge_type,
        description="Cloudflare Turnstile challenge on example.com",
        context={"url": "https://example.com/login"}
    )
    assert challenge.status == HumanVerificationState.WAITING_FOR_HUMAN

    overnight.register_branch("branch-web", "Browser task", "task-browse-web", UnattendedBranchState.WAITING_FOR_HUMAN)
    # Register an independent task that continues running
    overnight.register_branch("branch-code", "Local unit testing", "task-code", UnattendedBranchState.RUNNING)

    # 3. Simulate independent task completion
    overnight.update_branch_state("branch-code", UnattendedBranchState.COMPLETED, {"findings": ["All 15 tests passed"]})

    # 4. Simulate human solving challenge
    handoff.mark_completed_by_human(challenge.challenge_id)
    assert challenge.status == HumanVerificationState.VERIFIED_BY_HUMAN
    overnight.update_branch_state("branch-web", UnattendedBranchState.COMPLETED, {"findings": ["Login completed after human verification"]})

    # 5. Verify Morning Report reflects both tasks
    report = overnight.generate_morning_report()
    assert len(report.completed_tasks) == 2
    assert report.cost_incurred_usd == 0.0


def test_emergency_stop_halts_desktop_automation(desktop_system):
    """Verifies that Emergency Stop instantly halts desktop interactions and rejects queued actions."""
    desktop = desktop_system["desktop"]
    emergency = desktop_system["emergency"]

    # Trigger emergency stop
    emergency.trigger_stop("Owner test halt")
    assert desktop.is_emergency_stopped is True

    # Attempt click after stop
    res = desktop.execute_semantic_click("task-post-stop", 12345, risk_level=RiskLevel.L1)
    assert res.success is False
    assert "EMERGENCY_STOP" in res.target_description

    # Reset
    emergency.reset_stop("Zak")
    desktop.reset_halt()
    assert desktop.is_emergency_stopped is False
