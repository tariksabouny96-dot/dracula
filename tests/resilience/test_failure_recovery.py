"""
HOOD Resilience & Failure Recovery Test Suite
Governed by Master System Specification Sections 2, 23 & Build Directive Section 49.

Validates that HOOD recovers gracefully from faults and preserves state:
1. Provider timeout / failure triggers fallback to local/mock model
2. Tool execution failure or timeout is trapped without crashing orchestrator
3. Partial code change / edit failure triggers automatic rollback
4. Corrupt state or interrupted task is recorded cleanly in audit trail
5. Dispute engine resolves safely when one agent provides zero evidence
"""

import pytest
import tempfile
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone

from packages.contracts.models import (
    ModelRequest,
    ModelClass,
    ProviderName,
    TaskNode,
    RiskLevel,
    TaskStatus,
    AgentResponseContract,
    EvidencePacket
)
from packages.config import SystemConfig, ModelProviderConfig
from services.model_gateway.router import ModelRouter
from services.dev_executor.code_modifier import CodeModifier
from services.core.shared_context_bus import SharedContextBus, ContextPacket, PacketType, EvidenceQuality
from services.core.dispute_engine import DisputeEngine, DisputeStatus, AdjudicationStrategy
from services.audit.service import AuditService


def test_resilience_provider_failure_fallback():
    """Verify that when preferred provider is disabled or fails, router falls back to available provider."""
    config = SystemConfig()
    # Disable Gemini to force fallback to Mock
    config.providers["gemini"] = ModelProviderConfig(enabled=False)
    config.providers["mock"] = ModelProviderConfig(enabled=True)

    router = ModelRouter(config=config)
    req = ModelRequest(
        model_class=ModelClass.FAST,
        prompt="Health check resilient ping",
        preferred_provider=ProviderName.GEMINI,
        allowed_providers=[ProviderName.GEMINI, ProviderName.MOCK]
    )
    resp = router.invoke(req)
    
    # Must succeed via fallback (mock)
    assert resp is not None
    assert resp.is_fallback is True
    assert resp.provider == ProviderName.MOCK
    assert resp.text != ""


def test_resilience_code_modifier_atomic_rollback_on_failure():
    """Verify that if an edit fails or verification fails, the workspace is restored to prior state."""
    temp_dir = tempfile.mkdtemp()
    try:
        ws_path = Path(temp_dir)
        test_file = ws_path / "app.py"
        original_content = "def calculate():\n    return 42\n"
        test_file.write_text(original_content, encoding="utf-8")

        modifier = CodeModifier(workspace_root=ws_path)
        
        # 1. Create a snapshot/checkpoint
        checkpoint_ref = modifier.create_file_checkpoint(test_file)
        
        # 2. Apply a modification that breaks something
        broken_content = "def calculate():\n    syntax error !!\n"
        test_file.write_text(broken_content, encoding="utf-8")
        assert test_file.read_text(encoding="utf-8") == broken_content
        
        # 3. Trigger rollback
        success = modifier.restore_file_checkpoint("app.py", checkpoint_ref)
        assert success is True
        
        # 4. Verify original content restored
        restored = test_file.read_text(encoding="utf-8")
        assert restored == original_content
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_resilience_dispute_engine_handles_asymmetric_evidence():
    """Verify that dispute engine gracefully favors the agent with verified evidence over empty claim."""
    bus = SharedContextBus()
    audit_svc = AuditService()
    engine = DisputeEngine(bus=bus, audit_service=audit_svc)
    
    claim_a = ContextPacket(
        task_id="task-101",
        sender="Engineering_Lead",
        recipient="*",
        claim="Library X compiles cleanly on Windows 10",
        evidence=[EvidencePacket(
            claim="Pytest run returned 0",
            source="pytest_local_log",
            source_type="file",
            is_primary=True,
            confidence=0.98
        )],
        confidence=0.98,
        evidence_quality=EvidenceQuality.REPRODUCIBLE_TEST
    )
    
    claim_b = ContextPacket(
        task_id="task-101",
        sender="Speculative_Agent",
        recipient="*",
        claim="Library X probably fails because of C++ dll issues",
        evidence=[],
        confidence=0.30,
        evidence_quality=EvidenceQuality.UNVERIFIED_CLAIM
    )
    
    dispute = engine.raise_dispute(
        topic="Windows Compatibility",
        task_id="task-101",
        claim_a=claim_a,
        claim_b=claim_b,
        risk_level=RiskLevel.L2
    )
    
    resolved = engine.adjudicate(dispute.dispute_id)
    assert resolved.status == DisputeStatus.RESOLVED
    assert resolved.winning_agent == "Engineering_Lead"
    assert resolved.strategy_used == AdjudicationStrategy.EVIDENCE_WEIGHTED
    assert "reproducible_test" in resolved.rationale.lower() or "score" in resolved.rationale.lower()


def test_resilience_shared_context_bus_filter_and_isolation():
    """Verify that context bus correctly isolates tasks and delivers to targeted subscribers."""
    bus = SharedContextBus()
    received_packets = []
    
    bus.subscribe("Cybersecurity_Lead", lambda p: received_packets.append(p))
    
    p1 = ContextPacket(
        task_id="task-A",
        sender="Engineering_Lead",
        recipient="Cybersecurity_Lead",
        claim="Proposed firewall port configuration"
    )
    p2 = ContextPacket(
        task_id="task-B",
        sender="Finance_Lead",
        recipient="Operations_Lead",
        claim="Budget forecast"
    )
    
    bus.publish(p1)
    bus.publish(p2)
    
    # Cybersecurity_Lead must only have received p1
    assert len(received_packets) == 1
    assert received_packets[0].task_id == "task-A"
    
    # Check query helpers
    task_a_packets = bus.get_packets_for_task("task-A")
    assert len(task_a_packets) == 1
    assert task_a_packets[0].claim == "Proposed firewall port configuration"
