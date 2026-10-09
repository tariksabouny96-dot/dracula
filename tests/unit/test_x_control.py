import pytest
from pathlib import Path
from services.x_control.x_executive import (
    XExecutiveController,
    SecurityScopeViolationError,
    ScopeMutationDeniedError
)
from services.tool_gateway.gateway import PermissionDeniedError
from services.memory.service import MemoryService
from services.audit.service import AuditService

def test_x_activation_requires_confirmation(tmp_path):
    mem_svc = MemoryService(db_path=tmp_path / "x_test.db")
    audit_svc = AuditService(db_path=tmp_path / "x_test.db")
    x_ctrl = XExecutiveController(mem_svc, audit_svc)

    # Without confirmation -> must fail
    with pytest.raises(PermissionDeniedError):
        x_ctrl.wake_x(user="Zak", confirmed=False)

    # Non-Zak user -> must fail
    with pytest.raises(PermissionDeniedError):
        x_ctrl.wake_x(user="Attacker", confirmed=True)

    # Confirmed by Zak -> success
    res = x_ctrl.wake_x(user="Zak", confirmed=True)
    assert res["status"] == "X_ACTIVE"
    assert x_ctrl.is_active is True

    # Sleep X -> cleaned and dormant
    sleep_res = x_ctrl.sleep_x()
    assert sleep_res["status"] == "X_DORMANT"
    assert x_ctrl.is_active is False

def test_x_cannot_self_expand_scope(tmp_path):
    mem_svc = MemoryService(db_path=tmp_path / "x_test.db")
    audit_svc = AuditService(db_path=tmp_path / "x_test.db")
    x_ctrl = XExecutiveController(mem_svc, audit_svc)
    x_ctrl.wake_x(user="Zak", confirmed=True)

    with pytest.raises(ScopeMutationDeniedError):
        x_ctrl.attempt_self_expand_scope(["unauthorized-target.com"])

def test_x_enforces_versioned_scope(tmp_path):
    mem_svc = MemoryService(db_path=tmp_path / "x_test.db")
    audit_svc = AuditService(db_path=tmp_path / "x_test.db")
    x_ctrl = XExecutiveController(mem_svc, audit_svc)
    x_ctrl.wake_x(user="Zak", confirmed=True)

    # Load active scope
    scope_file = Path("policies/x-scope/example_engagement_scope_v1.yaml")
    x_ctrl.load_scope(scope_file)

    # In-scope target -> PASS
    assert x_ctrl.validate_target_in_scope("localhost", "recon") is True

    # Out-of-scope target -> BLOCKED
    with pytest.raises(SecurityScopeViolationError):
        x_ctrl.validate_target_in_scope("client-external.com", "recon")
