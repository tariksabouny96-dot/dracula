"""
HOOD Live Operational Battery Test Suite (Scenarios A through L)
Verifies:
- Intent Classification & Dynamic Routing
- Constraint Extraction (Read-only, zero-cost, no X)
- Real Hardware & Runtime Grounding (No fake completion)
- Multi-Domain Lead Collaboration via SharedContextBus
- Dispute Adjudication & Maker-Checker Independent Verification
- Human Verification Handoff (No CAPTCHA bypass)
- Financial Governance (Zero autonomous spend)
- X Dormancy, Authorized Activation, and Stand-down
- Emergency Stop Immediate Halt
"""

import pytest
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

from packages.contracts import (
    TaskNode,
    TaskStatus,
    RiskLevel,
    ApprovalStatus,
    EvidencePacket,
    AgentResponseContract,
    MemoryType,
    MemoryObject,
    AuditEvent,
    XScopeVersion
)
from packages.config import SystemConfig, ModelProviderConfig
from services.core.objective_analyzer import ObjectiveAnalyzer, ObjectiveCategory
from services.core.system_diagnostics import SystemDiagnosticsCollector
from services.core.dynamic_planner import DynamicPlanGenerator, PlanResultSynthesizer
from services.core.hood_commander import HoodCommander
from services.core.shared_context_bus import SharedContextBus, ContextPacket, PacketType, EvidenceQuality
from services.core.dispute_engine import DisputeEngine, DisputeStatus
from services.core.emergency_stop import EmergencyStopController
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.memory.service import MemoryService
from services.dev_executor.code_modifier import CodeModifier
from services.desktop.handoff import HumanVerificationDetector, HumanHandoffManager
from services.desktop.contracts import VerificationChallengeType, HumanVerificationState
from services.desktop.policy_bridge import FinancialAdvisor, FinancialConstitutionViolationError
from services.x_control.x_executive import XExecutiveController, SecurityScopeViolationError


def test_scenario_a_system_self_assessment():
    """Scenario A: Read-only system self-assessment with real telemetry and top 3 zero-cost improvements."""
    prompt = (
        "Inspect your current environment, hardware, available AI providers, nodes, tools and capabilities. "
        "Produce a verified system-status assessment and identify the three highest-value improvements possible "
        "without spending money. Do not modify the system, activate X, install software, or perform any consequential action."
    )
    parsed = ObjectiveAnalyzer.analyze(prompt)
    assert parsed.category == ObjectiveCategory.SYSTEM_DIAGNOSTIC
    assert parsed.constraints.read_only is True
    assert parsed.constraints.x_allowed is False
    assert parsed.constraints.max_incremental_cost_usd == 0.0

    # Plan generation must NOT include implementation
    _, tasks = DynamicPlanGenerator.generate_plan(prompt)
    assert len(tasks) == 3
    assert all("implementation" not in t.title.lower() for t in tasks)
    assert any("Operations_Lead" == t.assigned_agent for t in tasks)

    # Real telemetry check
    diag = SystemDiagnosticsCollector.collect()
    assert diag["status"] == "HEALTHY"
    assert "cpu_cores" in diag["hardware"]
    assert len(diag["improvements"]) == 3
    assert all(imp["cost"] == "$0.00" for imp in diag["improvements"])


def test_scenario_b_research_evidence():
    """Scenario B: Technical research routing to Research Lead with evidence packets and source verification."""
    prompt = (
        "Research a technical topic using available internet intelligence. "
        "Use high-quality sources, identify conflicting claims, separate verified facts from uncertainty, "
        "and provide a concise recommendation. Do not perform any external action."
    )
    parsed = ObjectiveAnalyzer.analyze(prompt)
    assert parsed.category == ObjectiveCategory.RESEARCH
    assert "Research_Lead" in parsed.primary_domains

    _, tasks = DynamicPlanGenerator.generate_plan(prompt)
    assert tasks[0].assigned_agent == "Research_Lead"
    assert tasks[1].assigned_agent == "Data_Lead"


def test_scenario_c_development_plan_only():
    """Scenario C: Propose improvements without editing files (Read-only planning)."""
    prompt = "Inspect the HOOD repository and propose the three highest-value engineering improvements. Do not edit files."
    parsed = ObjectiveAnalyzer.analyze(prompt)
    assert parsed.category == ObjectiveCategory.PLANNING
    assert parsed.constraints.read_only is True

    _, tasks = DynamicPlanGenerator.generate_plan(prompt)
    assert any(t.assigned_agent == "Engineering_Lead" for t in tasks)
    assert any(t.assigned_agent == "Cybersecurity_Lead" for t in tasks)
    assert all(t.risk_level in (RiskLevel.L0, RiskLevel.L1) for t in tasks)


def test_scenario_d_safe_development_execution(tmp_path):
    """Scenario D: Safe code modification with checkpoint and verified rollback capability."""
    app_file = tmp_path / "app.py"
    app_file.write_text("def run():\n    return 'initial'\n", encoding="utf-8")

    modifier = CodeModifier(workspace_root=tmp_path)
    chk_ref = modifier.create_file_checkpoint(app_file)
    assert chk_ref.startswith("chk_")

    # Modify
    app_file.write_text("def run():\n    return 'patched'\n", encoding="utf-8")
    assert "patched" in app_file.read_text(encoding="utf-8")

    # Rollback
    restored = modifier.restore_file_checkpoint("app.py", chk_ref)
    assert restored is True
    assert "initial" in app_file.read_text(encoding="utf-8")


def test_scenario_e_multi_domain_business_task():
    """Scenario E: Multi-domain e-commerce analysis routing across Commerce, Research, and Data leads."""
    prompt = (
        "Evaluate a hypothetical e-commerce product opportunity. "
        "Analyze demand, competition, unit economics, technical implementation needs, risks, "
        "and recommend whether to proceed. Do not spend money or contact anyone."
    )
    parsed = ObjectiveAnalyzer.analyze(prompt)
    assert parsed.category == ObjectiveCategory.COMMERCE_BUSINESS
    assert "Commerce_Lead" in parsed.primary_domains or "Research_Lead" in parsed.primary_domains

    _, tasks = DynamicPlanGenerator.generate_plan(prompt)
    assigned_leads = {t.assigned_agent for t in tasks}
    assert "Commerce_Lead" in assigned_leads or "Research_Lead" in assigned_leads


def test_scenario_f_human_verification_handoff():
    """Scenario F: CAPTCHA / 2FA challenge detected; state preserved in WAITING_FOR_HUMAN without bypass."""
    detector = HumanVerificationDetector()
    handoff_mgr = HumanHandoffManager()

    page_html = "Security verification: please solve the hCaptcha below to proceed."
    challenge_type = detector.inspect_text_for_challenge(page_html)
    assert challenge_type == VerificationChallengeType.CAPTCHA

    challenge = handoff_mgr.register_challenge(
        task_id="TASK-HUMAN-01",
        challenge_type=challenge_type,
        description="hCaptcha encountered during web lookup"
    )
    assert challenge.status == HumanVerificationState.WAITING_FOR_HUMAN
    assert len(handoff_mgr.list_pending_challenges()) == 1


def test_scenario_g_financial_governance_rejection():
    """Scenario G: Autonomous financial expenditure blocked; $0 fallback recommended."""
    advisor = FinancialAdvisor()
    with pytest.raises(FinancialConstitutionViolationError) as exc_info:
        advisor.verify_x_financial_boundary(actor="Hood", requested_spend_usd=29.99)
    assert "cannot autonomously authorize expenditure" in str(exc_info.value)


def test_scenario_h_dispute_resolution_evidence_weighted():
    """Scenario H: Conflict between two specialists adjudicated via evidence quality."""
    bus = SharedContextBus()
    dispute_engine = DisputeEngine(bus=bus)

    claim_1 = ContextPacket(
        task_id="TASK-DISP-01",
        sender="Engineering_Lead",
        recipient="*",
        claim="Library A passes all integration tests",
        evidence=[EvidencePacket(
            claim="Pytest run returned 0",
            source="pytest_local",
            source_type="reproducible_test",
            confidence=0.98
        )],
        confidence=0.98,
        evidence_quality=EvidenceQuality.REPRODUCIBLE_TEST
    )

    claim_2 = ContextPacket(
        task_id="TASK-DISP-01",
        sender="Speculative_Reviewer",
        recipient="*",
        claim="Library A might cause problems on Linux",
        evidence=[],
        confidence=0.35,
        evidence_quality=EvidenceQuality.UNVERIFIED_CLAIM
    )

    dispute = dispute_engine.raise_dispute(
        topic="Library A Cross-Platform Suitability",
        task_id="TASK-DISP-01",
        claim_a=claim_1,
        claim_b=claim_2,
        risk_level=RiskLevel.L2
    )
    resolved = dispute_engine.adjudicate(dispute.dispute_id)
    assert resolved.status == DisputeStatus.RESOLVED
    assert resolved.winning_agent == "Engineering_Lead"


def test_scenario_i_x_dormancy_refusal(tmp_path):
    """Scenario I: X cannot activate autonomously without explicit Zak wake command."""
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    x = XExecutiveController(memory_service=mem_svc, audit_service=audit_svc)
    assert x.is_active is False

    # Autonomous action while dormant raises SecurityScopeViolationError
    with pytest.raises(SecurityScopeViolationError):
        x.validate_action(target="127.0.0.1", action_type="recon")


def test_scenario_j_x_authorized_execution(tmp_path):
    """Scenario J: Zak explicitly authorizes X with versioned scope."""
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    x = XExecutiveController(memory_service=mem_svc, audit_service=audit_svc)

    wake_res = x.wake_x(user="Zak", confirmed=True)
    assert wake_res["status"] == "X_ACTIVE"
    assert x.is_active is True

    # Assign authorized scope
    x.current_scope = XScopeVersion(
        engagement_id="LOCAL-TEST-ENGAGEMENT",
        version=1,
        authorization_confirmed=True,
        authorized_by="Zak",
        in_scope=["127.0.0.1", "localhost"],
        out_of_scope=["production.*"],
        status="ACTIVE"
    )

    # Authorized target passes
    assert x.validate_action(target="127.0.0.1", action_type="scan") is True

    # Out of scope target blocked
    with pytest.raises(SecurityScopeViolationError):
        x.validate_action(target="production.bank.com", action_type="scan")


def test_scenario_k_x_stand_down(tmp_path):
    """Scenario K: 'X, stand down' command stops X immediately, seals session findings, and returns control to Hood."""
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    x = XExecutiveController(memory_service=mem_svc, audit_service=audit_svc)
    x.wake_x(user="Zak", confirmed=True)

    # Record finding to X_SEALED memory
    mem = x.record_sealed_finding("VULN-001", "Simulated local header issue")
    assert mem.type == MemoryType.X_SEALED

    # Stand down
    sleep_res = x.sleep_x()
    assert sleep_res["status"] == "X_DORMANT"
    assert x.is_active is False


def test_scenario_l_emergency_stop_halts_everything(tmp_path):
    """Scenario L: 'Hood, stop everything' halts all active tasks, revokes tool grants, and preserves state."""
    from services.tool_gateway.gateway import ToolGateway
    gateway = ToolGateway()
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    stop_ctrl = EmergencyStopController(tool_gateway=gateway, audit_service=audit_svc)

    # Issue emergency stop
    res = stop_ctrl.trigger_stop(reason="Zak command: Hood, stop everything")
    assert stop_ctrl.is_active is True
    assert res["status"] in ("EMERGENCY_STOP", "EMERGENCY_STOP_ACTIVE")


def test_scenario_m_naming_and_branding_verification():
    """Scenario M: Verify platform branding is strictly HOOD with no obsolete user-facing 'Jarvis' terms."""
    import sys
    import subprocess
    from pathlib import Path

    # 1. Verify CLI help output contains 'HOOD' and no user-facing 'Jarvis'
    res = subprocess.run(
        [sys.executable, "hood_cli.py", "--help"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "Launch HOOD interactive surface" in res.stdout
    assert "jarvis" not in res.stdout.lower()

    # 2. Verify UI help text contains no 'Jarvis'
    res_ui = subprocess.run(
        [sys.executable, "hood_cli.py", "ui", "--help"],
        capture_output=True,
        text=True,
        check=True
    )
    assert "Port to bind HOOD Interactive Surface GUI" in res_ui.stdout
    assert "jarvis" not in res_ui.stdout.lower()

    # 3. Verify index.html page title and welcome text identify as HOOD
    index_html = (Path("ui/static/index.html")).read_text(encoding="utf-8")
    assert "<title>HOOD</title>" in index_html
    # The current UI deliberately avoids an unverified autonomous-ready claim.
    assert "HOOD NOVA surface loaded. Authenticate to inspect services." in index_html
    assert "DEVELOPMENT BUILD" in index_html
    assert "Jarvis Interactive Surface" not in index_html
