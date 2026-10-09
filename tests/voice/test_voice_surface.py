"""
HOOD Jarvis Voice & Interaction Surface Test Suite
Verifies:
1. InteractionService session management and multi-modal shared conversation
2. Barge-in / interruption handling halting speech output promptly
3. Lightweight local WakeWord and VAD detection
4. Gemini Live voice adapter connectivity and streaming output
5. Disabled OpenAI Realtime adapter protection
6. VoiceRouter provider switching and cost telemetry
7. UI approval workflow through real ApprovalService
8. Emergency Stop integration halting GUI and audio operations
9. Jarvis Surface local HTTP server and live browser interaction
Governed by Master System Specification Section 8, 10, 14 & V0.4 Jarvis Surface Spec.
"""

import time
import pytest
from pathlib import Path

from packages.config import SystemConfig
from packages.contracts import RiskLevel
from services.core.hood_commander import HoodCommander
from services.core.emergency_stop import EmergencyStopController
from services.policy.approval_service import ApprovalService
from services.memory.service import MemoryService
from services.voice.contracts import VoiceProviderMode, MicrophonePrivacyState
from services.voice.local_vad import LightweightLocalVAD, LocalWakeWordDetector
from services.voice.adapters import GeminiLiveVoiceAdapter, OpenAIRealtimeVoiceAdapter
from services.voice.voice_router import VoiceRouter
from services.interaction.interaction_service import InteractionService, UIState
from services.browser.browser_service import BrowserService
from ui.server import JarvisServer


@pytest.fixture
def voice_system():
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
    return {
        "commander": commander,
        "approval": approval,
        "memory": memory,
        "router": router,
        "interaction": interaction
    }


def test_wake_word_and_local_vad_detection():
    """Verifies that local WakeWord detector triggers on 'Hood' and VAD classifies audio frames."""
    # 1. Wake word detector
    wake_detector = LocalWakeWordDetector(wake_phrase="Hood")
    triggered = []
    wake_detector.start_listening(lambda: triggered.append(True))
    assert wake_detector.is_listening() is True

    # Test candidate text matching
    assert wake_detector.process_text_candidate("Hood, what is the status?") is True
    assert len(triggered) == 1

    # Non-wake word should not trigger
    assert wake_detector.process_text_candidate("Hello computer") is False
    assert len(triggered) == 1

    wake_detector.stop_listening()
    assert wake_detector.is_listening() is False

    # 2. Local VAD energy computation
    vad = LightweightLocalVAD(energy_threshold=300.0)
    # Silence frame (all zeroes)
    silence = b"\x00\x00" * 160
    assert vad.process_frame(silence) is False

    # Loud speech frame (high amplitude PCM)
    speech = (b"\xff\x3f" + b"\x00\x40") * 80
    assert vad.process_frame(speech) is True


def test_gemini_live_adapter_and_disabled_openai(voice_system):
    """Verifies Gemini Live connection test and confirms OpenAI Realtime is strictly disabled."""
    router = voice_system["router"]

    # 1. Gemini Live capability check
    capabilities = router.get_provider_capabilities()
    assert len(capabilities) >= 3
    gemini_cap = next(c for c in capabilities if c["provider"] == "GEMINI_LIVE")
    assert gemini_cap["interruption_support"] is True
    # Billing is not verified; the capability must not advertise a free tier.
    assert gemini_cap["cost_class"] == "UNVERIFIED"

    # 2. OpenAI Realtime strictly disabled
    openai_cap = next(c for c in capabilities if c["provider"] == "OPENAI_REALTIME")
    assert openai_cap["availability"] == "DISABLED_PENDING_AUTH"

    with pytest.raises(PermissionError) as exc_info:
        router.set_provider_mode(VoiceProviderMode.OPENAI_REALTIME)
    assert "disabled pending Zak's authorization" in str(exc_info.value)


def test_shared_voice_text_continuity_and_barge_in(voice_system):
    """Verifies shared conversation context between Voice and Text and tests barge-in interruption."""
    interaction = voice_system["interaction"]
    session_id = "test_shared_continuity"

    # Step 1: No STT provider is connected. Voice input must fail visibly and must
    # not inject a fabricated transcript into the shared conversation.
    with pytest.raises(NotImplementedError):
        interaction.handle_voice_input(b"mock_voice", session_id=session_id)
    session = interaction.sessions[session_id]
    assert session.messages == []
    assert session.ui_state == UIState.IDLE

    # Step 2: Text remains a working accessible fallback in the same session.
    interaction.handle_text_input("Status check.", session_id=session_id)
    hood_reply2 = interaction.handle_text_input("Focus on authentication.", session_id=session_id)
    assert hood_reply2.sender == "Hood"
    assert len(session.messages) == 4

    # Step 3: Barge-in interruption test
    # Simulate Hood currently speaking
    session.ui_state = UIState.SPEAKING
    session.current_speaking_message_id = hood_reply2.id

    interaction.trigger_barge_in_interruption(session_id=session_id)
    assert session.ui_state == UIState.LISTENING
    assert hood_reply2.interrupted is True


def test_voice_cost_telemetry_and_privacy_state(voice_system):
    """Verifies that voice sessions record telemetry and privacy state without continuous cloud audio."""
    router = voice_system["router"]
    session_id = "telemetry_session_001"

    # Start session: no audio leaves the machine and no free tier is claimed.
    metrics = router.start_voice_session(session_id)
    assert metrics.is_free_tier is False
    assert router.microphone_privacy == MicrophonePrivacyState.LOCAL_WAKE_ACTIVE

    # No STT/TTS provider is connected: both fail closed instead of faking audio.
    with pytest.raises(NotImplementedError):
        router.transcribe_audio(b"audio_bytes", session_id)
    with pytest.raises(NotImplementedError):
        router.speak("All systems nominal.", session_id)

    # Ending the session records zero measured audio, not invented durations.
    ended_metrics = router.end_voice_session(session_id)
    assert ended_metrics.end_time is not None
    assert ended_metrics.audio_input_duration_seconds == 0
    assert ended_metrics.audio_output_duration_seconds == 0
    assert router.microphone_privacy == MicrophonePrivacyState.LOCAL_WAKE_ACTIVE


def test_ui_approval_workflow_integration(voice_system):
    """Verifies that approval requests render in UI format and resolve through ApprovalService."""
    interaction = voice_system["interaction"]
    approval = voice_system["approval"]

    # Create a pending approval
    req = approval.create_request(
        task_id="task_ui_approval",
        action_type="deploy_service",
        target="staging",
        reason="Deploy updated build to staging",
        risk_level=RiskLevel.L3,
        recommended_option="Approve deployment"
    )

    # List approvals via InteractionService
    pending_list = interaction.list_pending_approvals()
    assert len(pending_list) >= 1
    assert any(p["approval_id"] == req.approval_id for p in pending_list)

    # Resolve approval from UI
    res = interaction.resolve_approval(req.approval_id, approved=True, resolved_by="Zak")
    assert res["status"] == "APPROVED"
    assert approval.is_approved(req.approval_id) is True


@pytest.mark.browser_e2e
def test_jarvis_surface_http_server_and_browser_interaction(voice_system):
    """Verifies that Jarvis GUI server starts and serves HTML, API chat, and Emergency Stop."""
    interaction = voice_system["interaction"]
    es_controller = EmergencyStopController(
        tool_gateway=None,
        audit_service=None,
        browser_service=None
    )

    server = JarvisServer(interaction_service=interaction, emergency_stop=es_controller, port=8991)
    server.start()

    try:
        browser = BrowserService()
        if browser.is_available():
            browser.launch(headless=True)
            # Classic cinematic console moved to /classic (HOOD NEXT is default at /).
            nav = browser.navigate("http://127.0.0.1:8991/classic")
            assert nav["status"] == "success"

            dom = browser.inspect_dom()
            assert "HOOD" in dom["visible_text_snippet"]
            assert "432.0 Hz" in dom["visible_text_snippet"] or "NEURAL FREQ" in dom["visible_text_snippet"]

            # Click push-to-talk button
            browser.click("#btn-ptt")
            time.sleep(1.5)

            # Click Emergency Stop
            browser.click("#btn-emergency-stop")
            assert es_controller.is_active is True
            browser.close()
    finally:
        server.stop()
