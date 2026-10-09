"""F12 regression: Executive X state is durable across a restart.

A restart must never silently forget that X was active, must fail closed if a
persisted ACTIVE session's TTL has already elapsed, and must keep consumed
approval ids so an approval can never be replayed across the restart.
"""

import json
from datetime import datetime, timezone, timedelta

import pytest

from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.x_control.x_session_manager import XSessionManager, XOperationalState


def _activate(mgr, approval_svc, minutes=10):
    req = mgr.request_activation(requester_username="Zak", duration_minutes=minutes)
    approval_svc.resolve_request(req.approval_id, approved=True, resolved_by="Zak")
    mgr.activate(req.approval_id, approver_username="Zak")
    return req.approval_id


def test_active_state_survives_restart_within_ttl(tmp_path):
    state_path = tmp_path / "x_state.json"
    approval_svc = ApprovalService()
    audit = AuditService(db_path=tmp_path / "audit.db")

    mgr_a = XSessionManager(approval_service=approval_svc, audit_service=audit, state_path=state_path)
    _activate(mgr_a, approval_svc, minutes=30)
    assert mgr_a.get_status()["state"] == "ACTIVE_READ_ONLY"

    # Simulate a restart: a brand-new manager (fresh approval service too).
    mgr_b = XSessionManager(approval_service=ApprovalService(), audit_service=audit, state_path=state_path)
    status = mgr_b.get_status()
    assert status["state"] == "ACTIVE_READ_ONLY"
    assert status["remaining_seconds"] > 0


def test_expired_active_state_fails_closed_on_restart(tmp_path):
    state_path = tmp_path / "x_state.json"
    # Hand-craft a persisted ACTIVE session whose TTL is already in the past.
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    state_path.write_text(json.dumps({
        "state": "ACTIVE_READ_ONLY",
        "session_id": "x_sess_dead",
        "mode": "READ_ONLY_DIAGNOSTIC",
        "scope": "LOCAL_SANDBOX_ENV",
        "duration_seconds": 600,
        "activated_at": past,
        "expires_at": past,
        "authorized_by": "Zak",
        "pending_approval_id": None,
        "consumed_approval_ids": [],
    }))

    mgr = XSessionManager(approval_service=ApprovalService(), state_path=state_path)
    assert mgr.state == XOperationalState.DORMANT
    assert mgr.get_status()["state"] == "DORMANT"
    assert mgr.session_id is None


def test_consumed_approval_cannot_be_replayed_after_restart(tmp_path):
    state_path = tmp_path / "x_state.json"
    approval_svc = ApprovalService()
    audit = AuditService(db_path=tmp_path / "audit.db")

    mgr_a = XSessionManager(approval_service=approval_svc, audit_service=audit, state_path=state_path)
    approval_id = _activate(mgr_a, approval_svc, minutes=30)

    # Restart. The approval object itself is gone (in-memory), but the consumed
    # id must have survived so a replay is refused as consumed, never re-run.
    mgr_b = XSessionManager(approval_service=ApprovalService(), audit_service=audit, state_path=state_path)
    assert approval_id in mgr_b.consumed_approval_ids
    with pytest.raises(PermissionError):
        mgr_b.activate(approval_id, approver_username="Zak")
