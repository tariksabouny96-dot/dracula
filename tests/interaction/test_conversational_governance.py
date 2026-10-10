"""
Tests for Conversational Governance, Entity Disambiguation, and Authorization Boundaries.
Verifying:
1. Conversational questions do not execute tasks or missions.
2. User provisioning requests via chat are blocked and routed to User Admin.
3. Casual affirmations ("yes", "proceed") do not trigger unauthorized actions without pending approvals.
4. Multi-turn entity disambiguation (e.g., "Grey is my dog", "Do you know Grey?").
"""

import pytest
import tempfile
import gc
from pathlib import Path
from unittest.mock import MagicMock

from services.interaction.interaction_service import InteractionService, UIState
from services.policy.approval_service import ApprovalService
from services.memory.service import MemoryService
from services.auth.auth_service import AuthenticationService
from packages.contracts import RiskLevel, ApprovalRequest


@pytest.fixture
def governance_setup():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        mem_db = Path(td) / "test_memory.db"
        auth_db = Path(td) / "test_auth.db"

        memory_service = MemoryService(db_path=mem_db)
        auth_service = AuthenticationService(db_path=auth_db)
        auth_service.initialize_root_owner("zack", "Zack (Zakaria)", "ValidPass123!")
        approval_service = ApprovalService()

        # Mock commander with model router that returns fallback or simple text
        commander = MagicMock()
        commander.plan_objective.side_effect = RuntimeError("Commander.plan_objective should NOT be called!")

        interaction = InteractionService(
            commander=commander,
            approval_service=approval_service,
            memory_service=memory_service,
            auth_service=auth_service
        )

        yield interaction, memory_service, auth_service, approval_service, commander
        del interaction
        del memory_service
        del auth_service
        gc.collect()


def test_casual_affirmation_without_pending_approval(governance_setup):
    interaction, _, _, approval_service, _ = governance_setup
    assert len(approval_service.list_pending()) == 0

    # With nothing pending, "yes" is answered in the context of the conversation
    # (not with a canned "no pending approvals"), and it never approves anything.
    resp = interaction.handle_text_input("yes")
    assert "approval" not in resp.text.lower() or "no pending" not in resp.text.lower()
    assert interaction.get_ui_state() == UIState.IDLE
    resp2 = interaction.handle_text_input("proceed")
    assert resp2.sender == "Hood" and resp2.approval_ref is None
    assert len(approval_service.list_pending()) == 0


def test_casual_affirmation_with_pending_approval(governance_setup):
    interaction, _, _, approval_service, _ = governance_setup
    
    # Request a pending approval using create_request
    req = approval_service.create_request(
        task_id="task_test_01",
        action_type="FIREWALL_UPDATE",
        target="Port 8990",
        reason="Test firewall rule update",
        risk_level=RiskLevel.L2,
        recommended_option="ALLOW",
        options=[{"label": "ALLOW"}, {"label": "BLOCK"}]
    )
    assert len(approval_service.list_pending()) == 1

    resp = interaction.handle_text_input("yes")
    assert "active pending approval" in resp.text.lower()
    assert req.action_type in resp.text
    # Affirmation alone did not auto-resolve without proper approval channel
    assert len(approval_service.list_pending()) == 1


def test_user_creation_in_chat_is_strictly_blocked(governance_setup):
    interaction, _, auth_service, _, commander = governance_setup

    queries = [
        "Create a user named Alex",
        "Add user bob with password Secret123!",
        "Generate user credentials for Grey",
        "Give Grey manager access",
        "Grant Alex operator role",
        "Make Grey an admin"
    ]

    for q in queries:
        resp = interaction.handle_text_input(q)
        assert "user administration" in resp.text.lower() or "modal" in resp.text.lower()
        assert "cannot be performed through conversational chat" in resp.text.lower()

    # Verify zero users created in registry other than root owner
    root_user = auth_service.get_user_by_username("zack")
    assert root_user is not None
    registry = auth_service.list_users(requester_user_id=root_user["user_id"])
    assert len(registry) == 1
    assert registry[0]["username"] == "zack"

    # Verify commander plan_objective was NEVER called
    commander.plan_objective.assert_not_called()


def test_multi_turn_entity_disambiguation(governance_setup):
    interaction, memory_service, _, _, _ = governance_setup

    # Turn 1: Ask about Grey before HOOD knows Grey
    resp1 = interaction.handle_text_input("Do you know Grey?")
    assert "don't have any record of grey" in resp1.text.lower()

    # Turn 2: User states Grey is a dog
    resp2 = interaction.handle_text_input("Grey is my dog.")
    assert "recorded in memory that grey is your dog" in resp2.text.lower()

    # Turn 3: Ask again about Grey
    resp3 = interaction.handle_text_input("Do you know Grey?")
    assert "grey is zack's dog" in resp3.text.lower()

    # Turn 4: User mentions another entity
    resp4 = interaction.handle_text_input("Alice is my colleague.")
    assert "recorded in memory that alice is your colleague" in resp4.text.lower()

    # Turn 5: Ask about Alice
    resp5 = interaction.handle_text_input("Who is Alice?")
    assert "alice is zack's colleague" in resp5.text.lower()
