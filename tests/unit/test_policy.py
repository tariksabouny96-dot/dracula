import pytest
from packages.contracts import RiskLevel, ApprovalStatus
from services.policy.governance import RiskEvaluator
from services.policy.approval_service import ApprovalService

def test_risk_evaluation():
    # Public lookup -> L0
    assert RiskEvaluator.assess_risk("lookup", "public_url") == RiskLevel.L0
    # Safe test -> L1
    assert RiskEvaluator.assess_risk("run_test", "tests/unit") == RiskLevel.L1
    # File write -> L2
    assert RiskEvaluator.assess_risk("write_file", "src/main.py") == RiskLevel.L2
    # External comms -> L3
    assert RiskEvaluator.assess_risk("send_email", "client@corp.com", {"is_external_comm": True}) == RiskLevel.L3
    # Financial -> L4
    assert RiskEvaluator.assess_risk("purchase_subscription", "api_plan", {"is_financial": True}) == RiskLevel.L4
    # Destructive in prod -> L5
    assert RiskEvaluator.assess_risk("delete_database", "production_db", {"is_destructive": True, "is_production": True}) == RiskLevel.L5

def test_approval_service_silence_not_approved():
    service = ApprovalService()
    req = service.create_request(
        task_id="task-100",
        action_type="deploy_prod",
        target="production_cluster",
        reason="Release v1.0",
        risk_level=RiskLevel.L3
    )

    # Initial state is PENDING
    assert req.status == ApprovalStatus.PENDING
    # Silence is NEVER approval (H02)
    assert service.is_approved(req.approval_id) is False

    # Explicit resolution
    service.resolve_request(req.approval_id, approved=True, resolved_by="Zak")
    assert service.is_approved(req.approval_id) is True
