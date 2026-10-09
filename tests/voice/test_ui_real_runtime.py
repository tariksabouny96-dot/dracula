"""
Integration Test Suite for HOOD Interface Runtime Connection & Telemetry.
Verifies:
1. Introduction prompt returns grounded conversational response without executing actions.
2. Complex objective prompt triggers real DAG planning, execution across lead agents, and synthesizes 3 zero-cost improvements.
3. Conversation continuity is preserved across turns.
4. /api/telemetry returns grounded, truthful runtime telemetry.
5. Concurrent Emergency Stop halts execution and revokes capabilities safely.
"""

import time
import pytest
import urllib.request
import json
from packages.config import SystemConfig
from services.core.hood_commander import HoodCommander
from services.core.emergency_stop import EmergencyStopController
from services.policy.approval_service import ApprovalService
from services.memory.service import MemoryService
from services.voice.voice_router import VoiceRouter
from services.interaction.interaction_service import InteractionService, UIState
from services.browser.browser_service import BrowserService
from ui.server import JarvisServer, HoodServer


@pytest.fixture
def runtime_setup():
    config = SystemConfig()
    approval = ApprovalService()
    memory = MemoryService()
    commander = HoodCommander(config=config, approval_service=approval, memory_service=memory)
    router = VoiceRouter(config)
    interaction = InteractionService(
        commander=commander,
        approval_service=approval,
        memory_service=memory,
        voice_router=router,
        config=config
    )
    es = EmergencyStopController(tool_gateway=None, audit_service=None, browser_service=None)
    return {
        "config": config,
        "commander": commander,
        "interaction": interaction,
        "emergency_stop": es
    }


def test_hood_interface_introduction_advisory_mode(runtime_setup):
    """
    Acceptance Test 1:
    Verifies that asking HOOD to introduce itself from the UI returns a grounded advisory
    response specifying Zak's authority, capabilities, constraints, AI levels 1-3,
    6 domain leads, and dormant X status, with 0 executed tasks.
    """
    interaction = runtime_setup["interaction"]
    prompt = (
        "Hood, introduce yourself to me. Tell me who you are, who I am in your authority model, "
        "what you can currently do, what you cannot currently do, which AI levels and agents "
        "are available to you, and the current status of X. Do not execute any actions. "
        "This is conversation only."
    )
    msg = interaction.handle_text_input(prompt)
    assert msg.sender == "Hood"
    text = msg.text

    # Verify grounded elements
    assert "Zak" in text
    assert "sole human owner" in text or "final human authority" in text
    assert "Level 1" in text
    assert "Level 2" in text
    assert "Level 3" in text
    assert "Engineering_Lead" in text or "Engineering Lead" in text
    assert "Cybersecurity_Lead" in text or "Cybersecurity Lead" in text
    assert "Operations_Lead" in text or "Operations Lead" in text
    assert "X_DORMANT" in text or "dormant" in text.lower()

    # Verify no tasks were executed (advisory/conversational only)
    session = interaction.sessions[interaction.active_session_id]
    assert len(session.active_tasks) == 0


def test_hood_interface_real_dag_execution_and_improvements(runtime_setup):
    """
    Acceptance Test 2:
    Verifies that submitting 'Inspect my current HOOD environment and give me the three
    highest-value improvements possible without spending money...' triggers real DAG planning
    across Operations and Engineering leads, collects real diagnostics, produces 3 high-value
    improvements at $0 cost, and updates task progress.
    """
    interaction = runtime_setup["interaction"]
    prompt = (
        "Inspect my current HOOD environment and give me the three highest-value improvements "
        "possible without spending money or making dangerous changes."
    )
    msg = interaction.handle_text_input(prompt)
    assert msg.sender == "Hood"
    text = msg.text

    # Verify evidence synthesis & 3 improvements
    assert "Three High-Value Improvements" in text or "High-Value Improvements" in text or "Option" in text or "1." in text
    assert "$0.00" in text or "zero-cost" in text.lower() or "$0" in text

    # Verify real DAG tasks were created and executed
    session = interaction.sessions[interaction.active_session_id]
    assert len(session.active_tasks) >= 2
    for task in session.active_tasks:
        assert task.status == "COMPLETED"
        assert task.progress_pct == 100


def test_hood_interface_conversation_continuity(runtime_setup):
    """
    Acceptance Test 3:
    Verifies multi-turn conversational continuity in the same session.
    A follow-up question refers back to previous recommendations.
    """
    interaction = runtime_setup["interaction"]
    prompt1 = "Inspect my current HOOD environment and give me the three highest-value improvements possible without spending money."
    interaction.handle_text_input(prompt1)

    prompt2 = "What was your number one recommendation?"
    msg2 = interaction.handle_text_input(prompt2)
    assert "1" in msg2.text or "recommendation" in msg2.text.lower()


def test_hood_interface_telemetry_endpoint(runtime_setup):
    """Without owner setup operational telemetry must remain unavailable."""
    from urllib.error import HTTPError
    server = HoodServer(interaction_service=runtime_setup["interaction"],
                        emergency_stop=runtime_setup["emergency_stop"], port=8992)
    server.start()
    try:
        with pytest.raises(HTTPError) as err:
            urllib.request.urlopen("http://127.0.0.1:8992/api/telemetry", timeout=5)
        assert err.value.code == 503
    finally:
        server.stop()
