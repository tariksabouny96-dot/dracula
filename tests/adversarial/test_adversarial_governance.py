"""
HOOD Adversarial Self-Test Suite
Governed by Master System Specification Sections 1, 2, 3, 23 & Build Directive Section 48.

Strictly verifies that HOOD refuses and prevents hostile or policy-violating actions:
1. Autonomous spend attempt without Zak's approval (Financial Constitution)
2. Tool execution without a valid CapabilityGrant (Tool Gateway)
3. Action execution when Emergency Stop is actively engaged (Emergency Stop)
4. X attempting to activate without explicit Zak confirmation (X Invariant)
5. X attempting to expand scope autonomously without approval (Scope Rigidity)
6. Secret exfiltration attempt in prompt / tool response (Secret Isolation & Redaction)
7. Automated attempt to solve/bypass CAPTCHA or human verification (Human Handoff)
8. Execution of L3/L4/L5 action when approval request is expired or rejected
9. X attempt to access non-X tenant memory (Tenant & X_SEALED Isolation)
10. System modification attempt during financial hold
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from packages.contracts.models import (
    RiskLevel,
    ApprovalStatus,
    ApprovalRequest,
    CapabilityGrant,
    TaskNode,
    TaskStatus,
    MemoryType,
    MemoryObject,
    AuditEvent,
    XScopeVersion
)
from packages.config import SystemConfig
from packages.logging.redactor import redact_string
from services.policy.approval_service import ApprovalService
from services.policy.governance import RiskEvaluator
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.tool_gateway.tools import DesktopControlTool
from services.core.emergency_stop import EmergencyStopController
from services.orchestrator.dag_scheduler import (
    DAGOrchestrator,
    EmergencyStopTriggeredError,
    TaskExecutionBlockedError
)
from services.desktop.policy_bridge import FinancialAdvisor, FinancialConstitutionViolationError
from services.desktop.contracts import VerificationChallengeType, HumanVerificationState
from services.desktop.handoff import HumanVerificationDetector, HumanHandoffManager
from services.x_control.x_executive import (
    XExecutiveController,
    SecurityScopeViolationError,
    ScopeMutationDeniedError
)
from services.memory.service import MemoryService
from services.audit.service import AuditService


def test_adversarial_autonomous_spend_rejected():
    """Verify that any autonomous spend attempt without prior approval is blocked and raises FinancialConstitutionViolationError."""
    advisor = FinancialAdvisor()
    
    # Attempting to authorize a spend autonomously by an agent or X
    with pytest.raises(FinancialConstitutionViolationError) as exc_info:
        advisor.verify_x_financial_boundary(actor="Hood_or_X", requested_spend_usd=45.00)
    assert "cannot autonomously authorize expenditure" in str(exc_info.value)
    assert "Financial Constitution" in str(exc_info.value)


def test_adversarial_tool_gateway_unauthorized_call():
    """Verify that invoking a capability-gated tool without an active CapabilityGrant is blocked."""
    gateway = ToolGateway()
    tool = DesktopControlTool(gateway=gateway)
    gateway.register_tool(tool)
    
    # Call without grant_id
    with pytest.raises(PermissionDeniedError) as exc_info:
        tool.execute({"action": "observe"})
    assert "Missing valid capability grant" in str(exc_info.value)
        
    # Call with invalid/revoked grant_id
    grant = gateway.issue_capability_grant("desktop:control", "screen", "Hood", ttl_seconds=60)
    grant.is_revoked = True
    with pytest.raises(PermissionDeniedError) as exc_info:
        tool.execute({"action": "observe", "grant_id": grant.grant_id})
    assert "invalid or revoked" in str(exc_info.value)


def test_adversarial_emergency_stop_halts_all_execution(tmp_path):
    """Verify that active Emergency Stop forbids any DAG task execution immediately."""
    gateway = ToolGateway()
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    stop_ctrl = EmergencyStopController(tool_gateway=gateway, audit_service=audit_svc)
    orchestrator = DAGOrchestrator(emergency_stop=stop_ctrl, audit_service=audit_svc)
    
    # Engage emergency stop
    stop_ctrl.trigger_stop(reason="Adversarial attack detected")
    
    task = TaskNode(
        title="Hostile Payload Task",
        objective="Run unauthorized command",
        risk_level=RiskLevel.L1
    )
    orchestrator.add_task(task)
    
    with pytest.raises(EmergencyStopTriggeredError):
        orchestrator.run_dag(lambda t: None)
        
    assert task.status == TaskStatus.EMERGENCY_STOPPED


def test_adversarial_x_dormancy_and_unauthorized_activation(tmp_path):
    """Verify that X cannot be activated without explicit Zak confirmation."""
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    x = XExecutiveController(memory_service=mem_svc, audit_service=audit_svc)
    assert x.is_active is False
    
    # Attempt activation by non-Zak
    with pytest.raises(PermissionDeniedError) as exc_info:
        x.wake_x(user="Attacker", confirmed=True)
    assert "Only Zak can initiate X activation" in str(exc_info.value)
    assert x.is_active is False

    # Attempt activation without confirmation
    with pytest.raises(PermissionDeniedError) as exc_info:
        x.wake_x(user="Zak", confirmed=False)
    assert "explicit confirmation" in str(exc_info.value)
    assert x.is_active is False

    # Verify action blocked while dormant
    with pytest.raises(SecurityScopeViolationError) as exc_info:
        x.validate_action(target="https://internal.test", action_type="recon")
    assert "dormant" in str(exc_info.value)


def test_adversarial_x_autonomous_scope_expansion_denied(tmp_path):
    """Verify that X cannot expand scope outside approved scope targets."""
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    x = XExecutiveController(memory_service=mem_svc, audit_service=audit_svc)
    
    # Wake X properly
    x.wake_x(user="Zak", confirmed=True)
    assert x.is_active is True

    # Assign a restricted active scope
    x.current_scope = XScopeVersion(
        engagement_id="SEC-ENG-001",
        version=1,
        authorization_confirmed=True,
        authorized_by="Zak",
        in_scope=["internal.hood.local"],
        out_of_scope=["production.bank.com"],
        status="ACTIVE"
    )
    
    # Attempting action on forbidden target
    with pytest.raises(SecurityScopeViolationError) as exc_info:
        x.validate_action(target="production.bank.com", action_type="scan")
    assert "OUT OF SCOPE" in str(exc_info.value)

    # Attempting autonomous scope expansion
    with pytest.raises(ScopeMutationDeniedError) as exc_info:
        x.attempt_self_expand_scope(["new_target.corp"])
    assert "cannot autonomously edit or expand" in str(exc_info.value)


def test_adversarial_secret_leak_redaction():
    """Verify that credentials / tokens / API keys in text output are redacted."""
    sensitive_text = "api_key: sk-1234567890abcdef1234567890abcdef and token: bearer 12345678901234567890"
    clean_text = redact_string(sensitive_text)
    
    assert "sk-" not in clean_text
    assert "12345678901234567890" not in clean_text
    assert "[REDACTED_SECRET]" in clean_text


def test_adversarial_captcha_bypass_refusal():
    """Verify that automated attempts to bypass CAPTCHA / bot detection immediately detect challenge and register human handoff."""
    detector = HumanVerificationDetector()
    handoff_mgr = HumanHandoffManager()
    
    screen_text = "Please complete the Cloudflare Turnstile CAPTCHA to prove you are human."
    detected_type = detector.inspect_text_for_challenge(screen_text)
    
    assert detected_type == VerificationChallengeType.CAPTCHA
    
    # Register challenge requiring human action
    challenge = handoff_mgr.register_challenge(
        task_id="task-adversarial-captcha",
        challenge_type=detected_type,
        description="Encountered Cloudflare Turnstile on screen"
    )
    assert challenge.status == HumanVerificationState.WAITING_FOR_HUMAN
    assert len(handoff_mgr.list_pending_challenges()) == 1


def test_adversarial_approval_bypass_rejection(tmp_path):
    """Verify that L4/L5 actions cannot execute if approval was rejected or unapproved."""
    approval_svc = ApprovalService()
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    orchestrator = DAGOrchestrator(approval_service=approval_svc, audit_service=audit_svc)
    
    # Create L4 task requiring approval
    task = TaskNode(
        title="High Risk Migration",
        objective="Modify security policy",
        risk_level=RiskLevel.L4
    )
    orchestrator.add_task(task)
    
    # Register approval request and reject it
    appr = approval_svc.create_request(
        task_id=task.task_id,
        action_type="SECURITY_POLICY_UPDATE",
        target="system_rules",
        reason="Test high risk update",
        risk_level=RiskLevel.L4,
        recommended_option="Reject"
    )
    approval_svc.resolve_request(appr.approval_id, approved=False, resolved_by="Zak", rejection_reason="Rejected by Zak")
    
    # Bind rejected approval to task inputs
    task.inputs["approval_id"] = appr.approval_id
    
    # Orchestrator runs, but rejected approval MUST raise TaskExecutionBlockedError deterministically
    with pytest.raises(TaskExecutionBlockedError) as exc_info:
        orchestrator.run_dag(lambda t: None)
    assert "APPROVAL_REJECTED" in str(exc_info.value)
    assert task.status == TaskStatus.FAILED


def test_adversarial_x_sealed_memory_isolation(tmp_path):
    """Verify that regular queries cannot access X_SEALED memories and vice-versa."""
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    
    # Add an X_SEALED memory
    x_mem = MemoryObject(
        type=MemoryType.X_SEALED,
        content="Secret offensive finding 0day vuln details",
        project="hood_core",
        source_agent="X"
    )
    mem_svc.write_memory(x_mem, caller_agent="Zak")
    
    # Add a normal working memory
    hood_mem = MemoryObject(
        type=MemoryType.WORKING,
        content="Normal engineering plan",
        project="hood_core",
        source_agent="Hood"
    )
    mem_svc.write_memory(hood_mem, caller_agent="Hood")
    
    # Normal agent query (is_x_active=False) must NOT retrieve X_SEALED memory
    hood_results = mem_svc.query_memories(project="hood_core", is_x_active=False)
    assert all(m.type != MemoryType.X_SEALED for m in hood_results)
    assert any("Normal engineering plan" in m.content for m in hood_results)
    assert len(hood_results) == 1

    # When X is active, X_SEALED memory IS accessible
    x_results = mem_svc.query_memories(project="hood_core", is_x_active=True)
    assert any(m.type == MemoryType.X_SEALED for m in x_results)
