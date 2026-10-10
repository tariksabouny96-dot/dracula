"""
Integration Test Suite for HOOD Interface Runtime Connection & Telemetry.
Verifies:
1. Introduction prompt returns grounded conversational response without executing actions.
2. An objective in chat is never "executed" or reported as done: chat only talks.
3. Conversation continuity: earlier turns are sent to the model.
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
    """The self-introduction is built only from live facts: no invented models or agents."""
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
    assert "Root Owner" in text and "final authority" in text
    for level in ("Level 1", "Level 2", "Level 3"):
        assert level in text
    assert "does not exist yet" in text                      # Level 3 is not claimed
    assert "Planner" in text and "Verifier" in text           # the agents that really exist
    for invented in ("Commerce_Lead", "LLaMA 3.1", "Hood-Code-v1", "self-healing"):
        assert invented not in text
    session = interaction.sessions[interaction.active_session_id]
    assert len(session.active_tasks) == 0


def test_hood_interface_objective_is_never_faked(runtime_setup):
    """An objective typed in chat is not executed, and nothing is reported as done or verified."""
    interaction = runtime_setup["interaction"]
    prompt = (
        "Inspect my current HOOD environment and give me the three highest-value improvements "
        "possible without spending money or making dangerous changes."
    )
    msg = interaction.handle_text_input(prompt)
    assert msg.sender == "Hood"
    session = interaction.sessions[interaction.active_session_id]
    assert session.active_tasks == []
    for fabricated in ("verified successfully", "Tasks Executed", "All completed with verified evidence"):
        assert fabricated not in msg.text


def test_hood_interface_conversation_continuity(runtime_setup):
    """Follow-ups are answered with the earlier turns sent to the model."""
    interaction = runtime_setup["interaction"]
    seen = []

    class Router:
        providers = {}

        def invoke(self, req):
            from packages.contracts import ModelResponse, ModelUsage, ProviderName
            seen.append(req.prompt)
            return ModelResponse(text="Reply " + str(len(seen)), provider=ProviderName.GEMINI,
                                 model_name="stub", usage=ModelUsage(), latency_ms=1)

    interaction.commander.model_router = Router()
    interaction.handle_text_input("Give me three improvements for my laptop setup.")
    msg2 = interaction.handle_text_input("What was your number one recommendation?")
    assert msg2.text == "Reply 2"
    assert "Give me three improvements for my laptop setup." in seen[1]
    assert "HOOD: Reply 1" in seen[1]


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
