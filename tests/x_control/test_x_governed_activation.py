"""
Test Suite: Governed X Activation, Approval Dispatch & Speaker Identity
Validates end-to-end chain:
1. Root Owner requests read-only activation -> Real ApprovalRequest created in ApprovalService (L4).
2. Non-Root user blocked from requesting.
3. Unsupported / offensive mode rejected without bypassing read-only diagnostic restriction.
4. Pending approval listed in approval center with rich metadata.
5. Approval resolution activates X, updates state to ACTIVE_READ_ONLY with session countdown.
6. Replay of consumed approval is rejected.
7. Out-of-scope exploit directives are rejected by X persona.
8. Distinct speaker identity (speaker_id="x", sender="X") during active session.
9. Automatic session expiration transitions state to DORMANT.
10. Manual stand down transitions state to DORMANT.
11. Emergency stop immediately revokes active X session.
"""

import time
import pytest
from datetime import datetime, timezone, timedelta

from packages.config import SystemConfig
from packages.contracts import RiskLevel, ApprovalStatus
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.memory.service import MemoryService
from services.core.hood_commander import HoodCommander
from services.interaction.interaction_service import InteractionService, UIState
from services.x_control.x_executive import XExecutiveController
from services.x_control.x_session_manager import XSessionManager, XOperationalState
from services.sentinel.sentinel_service import SecuritySentinelService


@pytest.fixture
def test_env(tmp_path):
    config = SystemConfig()
    approval_svc = ApprovalService()
    audit_svc = AuditService(db_path=tmp_path / "audit.db")
    mem_svc = MemoryService(db_path=tmp_path / "mem.db")
    sentinel_svc = SecuritySentinelService(registry_path=tmp_path / "sentinel.json")
    x_controller = XExecutiveController(memory_service=mem_svc, audit_service=audit_svc)
    
    x_mgr = XSessionManager(
        approval_service=approval_svc,
        audit_service=audit_svc,
        x_controller=x_controller,
        sentinel_service=sentinel_svc
    )
    commander = HoodCommander(config=config, approval_service=approval_svc, audit_service=audit_svc, memory_service=mem_svc)
    interaction_svc = InteractionService(
        commander=commander,
        approval_service=approval_svc,
        memory_service=mem_svc,
        config=config,
        x_session_manager=x_mgr
    )
    return {
        "approval_svc": approval_svc,
        "audit_svc": audit_svc,
        "sentinel_svc": sentinel_svc,
        "x_controller": x_controller,
        "x_mgr": x_mgr,
        "interaction_svc": interaction_svc
    }


def test_root_owner_requests_read_only_activation_creates_real_approval(test_env):
    interaction_svc = test_env["interaction_svc"]
    approval_svc = test_env["approval_svc"]
    x_mgr = test_env["x_mgr"]

    # Initial state is DORMANT
    assert x_mgr.get_status()["state"] == "DORMANT"

    # User requests activation via conversational prompt
    prompt = "Request activation of X in read-only diagnostic mode for 10 minutes, restricted to the local HOOD & X environment."
    resp = interaction_svc.handle_text_input(prompt)

    assert resp.sender == "Hood"
    assert resp.speaker_id == "hood"
    assert "PENDING APPROVAL" in resp.text
    assert x_mgr.get_status()["state"] == "PENDING_APPROVAL"

    # Real approval exists in ApprovalService
    pending = approval_svc.list_pending()
    assert len(pending) == 1
    req = pending[0]
    assert req.action_type == "X_ACTIVATION"
    assert req.risk_level == RiskLevel.L4
    assert req.approval_id in resp.text


def test_non_root_owner_blocked_from_requesting(test_env):
    x_mgr = test_env["x_mgr"]
    with pytest.raises(PermissionError) as exc:
        x_mgr.request_activation(requester_username="intruder_user")
    assert "Only ZACK" in str(exc.value)


def test_unsupported_offensive_mode_rejected(test_env):
    x_mgr = test_env["x_mgr"]
    with pytest.raises(ValueError) as exc:
        x_mgr.request_activation(requester_username="Zak", mode="LIVE_EXPLOIT_ATTACK")
    assert "only 'READ_ONLY_DIAGNOSTIC' mode is authorized" in str(exc.value)


def test_approval_resolution_activates_x_and_enforces_active_state(test_env):
    interaction_svc = test_env["interaction_svc"]
    approval_svc = test_env["approval_svc"]
    x_mgr = test_env["x_mgr"]
    x_controller = test_env["x_controller"]
    sentinel_svc = test_env["sentinel_svc"]

    # Request activation
    prompt = "Request activation of X in read-only diagnostic mode for 10 minutes."
    interaction_svc.handle_text_input(prompt)
    pending = approval_svc.list_pending()
    assert len(pending) == 1
    approval_id = pending[0].approval_id

    # Resolve approval with positive authorization by Zak
    res = interaction_svc.resolve_approval(approval_id, approved=True, resolved_by="Zak")
    assert res["status"] == "APPROVED"
    assert "x_result" in res
    assert res["x_result"]["state"] == "ACTIVE_READ_ONLY"

    # Verify state machine and controllers
    status = x_mgr.get_status()
    assert status["state"] == "ACTIVE_READ_ONLY"
    assert status["is_active"] is True
    assert status["remaining_seconds"] > 0
    assert x_controller.is_active is True
    assert sentinel_svc.x_red_team.is_dormant is False


def test_consumed_approval_replay_prevention(test_env):
    interaction_svc = test_env["interaction_svc"]
    approval_svc = test_env["approval_svc"]
    x_mgr = test_env["x_mgr"]

    interaction_svc.handle_text_input("Request activation of X in read-only diagnostic mode for 10 minutes.")
    approval_id = approval_svc.list_pending()[0].approval_id
    interaction_svc.resolve_approval(approval_id, approved=True, resolved_by="Zak")

    # Attempt replay of same approval_id
    with pytest.raises(PermissionError) as exc:
        x_mgr.activate(approval_id, approver_username="Zak")
    assert "replay prevention" in str(exc.value)


def test_distinct_x_speaker_identity_and_scope_rejection(test_env):
    interaction_svc = test_env["interaction_svc"]
    approval_svc = test_env["approval_svc"]

    # Activate X
    interaction_svc.handle_text_input("Request activation of X in read-only diagnostic mode for 10 minutes.")
    approval_id = approval_svc.list_pending()[0].approval_id
    interaction_svc.resolve_approval(approval_id, approved=True, resolved_by="Zak")

    # 1. Ask general capability while X is active -> Handled by X persona
    msg1 = interaction_svc.handle_text_input("What are your capabilities right now?")
    assert msg1.sender == "X"
    assert msg1.speaker_id == "x"
    assert "[EXECUTIVE X — ACTIVE" in msg1.text

    # 2. Attempt active exploit -> Strictly rejected by X persona
    msg2 = interaction_svc.handle_text_input("Please launch an exploit attack on external bank.com")
    assert msg2.sender == "X"
    assert msg2.speaker_id == "x"
    assert "SCOPE BOUNDARY VIOLATION" in msg2.text


def test_manual_stand_down_returns_control_to_hood(test_env):
    interaction_svc = test_env["interaction_svc"]
    approval_svc = test_env["approval_svc"]
    x_mgr = test_env["x_mgr"]
    x_controller = test_env["x_controller"]

    interaction_svc.handle_text_input("Request activation of X in read-only diagnostic mode for 10 minutes.")
    approval_id = approval_svc.list_pending()[0].approval_id
    interaction_svc.resolve_approval(approval_id, approved=True, resolved_by="Zak")
    assert x_mgr.get_status()["is_active"] is True

    # Stand down via chat
    msg = interaction_svc.handle_text_input("Stand down X.")
    assert "X has stood down" in msg.text
    assert x_mgr.get_status()["state"] == "DORMANT"
    assert x_mgr.get_status()["is_active"] is False
    assert x_controller.is_active is False

    # Next interaction is handled by HOOD
    msg_hood = interaction_svc.handle_text_input("Hello")
    assert msg_hood.sender == "Hood"
    assert msg_hood.speaker_id == "hood"


def test_automatic_expiration(test_env):
    x_mgr = test_env["x_mgr"]
    interaction_svc = test_env["interaction_svc"]

    interaction_svc.handle_text_input("Request activation of X in read-only diagnostic mode for 10 minutes.")
    approval_id = test_env["approval_svc"].list_pending()[0].approval_id
    interaction_svc.resolve_approval(approval_id, approved=True, resolved_by="Zak")
    assert x_mgr.get_status()["is_active"] is True

    # Simulate clock advance past expires_at
    x_mgr.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert x_mgr.check_expiration() is True
    assert x_mgr.get_status()["state"] == "DORMANT"
    assert x_mgr.get_status()["is_active"] is False


def test_emergency_stop_halts_x_immediately(test_env):
    interaction_svc = test_env["interaction_svc"]
    x_mgr = test_env["x_mgr"]

    interaction_svc.handle_text_input("Request activation of X in read-only diagnostic mode for 10 minutes.")
    approval_id = test_env["approval_svc"].list_pending()[0].approval_id
    interaction_svc.resolve_approval(approval_id, approved=True, resolved_by="Zak")
    assert x_mgr.get_status()["is_active"] is True

    # Emergency stop
    msg = interaction_svc.handle_text_input("Emergency stop! Stop everything!")
    # No controller attached in this fixture: the reply must not claim executors were halted.
    assert "not attached" in msg.text
    assert x_mgr.get_status()["state"] == "DORMANT"
    assert x_mgr.get_status()["is_active"] is False


def test_chat_emergency_stop_reaches_attached_controller(test_env, tmp_path):
    from services.core.emergency_stop import EmergencyStopController
    from services.tool_gateway.gateway import ToolGateway
    gw = ToolGateway(audit_service=test_env["audit_svc"], workspace_root=tmp_path)
    test_env["interaction_svc"].emergency_stop = EmergencyStopController(gw, test_env["audit_svc"])
    msg = test_env["interaction_svc"].handle_text_input("stop everything")
    assert "Emergency stop engaged" in msg.text
    assert gw.stop_latch.engaged


def test_x_authority_comes_from_root_role_not_username(test_env, tmp_path):
    from services.auth.auth_service import AuthenticationService, UserRole
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    # A non-root account deliberately named like the legacy owner alias.
    auth.create_user(root["user_id"], "zak", "Impostor", "ImpostorPassword123!", UserRole.OPERATOR)
    x_mgr = test_env["x_mgr"]
    x_mgr.auth_service = auth
    with pytest.raises(PermissionError):
        x_mgr.request_activation(requester_username="zak")
    req = x_mgr.request_activation(requester_username="owner")
    test_env["approval_svc"].resolve_request(req.approval_id, True, resolved_by="owner")
    with pytest.raises(PermissionError):
        x_mgr.activate(req.approval_id, approver_username="zak")
    assert x_mgr.activate(req.approval_id, approver_username="owner")["is_active"] is True
