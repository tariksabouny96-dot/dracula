"""
HOOD Voice Providers - Gemini Live & Disabled OpenAI Realtime Adapters
Governed by Master System Specification Section 8 & V0.4 Voice Router Spec.
"""

import time
import uuid
from typing import Dict, Any, List, Optional, Callable
from services.voice.contracts import (
    VoiceProviderMode,
    VoiceSessionState,
    STTProvider,
    VoiceOutputProvider,
    STTResult,
    AudioOutputEvent
)
from packages.config import SystemConfig


class GeminiLiveVoiceAdapter(STTProvider, VoiceOutputProvider):
    """
    Adapter for Gemini conversational voice capabilities.
    Manages session lifecycle, latency tracking, interruption, and free-tier reporting.
    Connects to Gemini API endpoints using existing vault credentials.
    """
    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()
        self.provider_mode = VoiceProviderMode.GEMINI_LIVE
        self._active_sessions: Dict[str, Dict[str, Any]] = {}
        self._is_connected = False
        self._speaking_cancelled: Dict[str, bool] = {}

    def connect(self) -> bool:
        """Verifies connectivity to Google Gemini endpoint."""
        from services.model_gateway.gemini_adapter import GeminiProviderAdapter
        from packages.auth.vault import SecretVault
        gemini = GeminiProviderAdapter(vault=SecretVault())
        self._is_connected = gemini.is_healthy()
        return self._is_connected

    def is_connected(self) -> bool:
        return self._is_connected

    def start_session(self, session_id: Optional[str] = None) -> str:
        sid = session_id or f"gemini_voice_{uuid.uuid4().hex[:8]}"
        self._active_sessions[sid] = {
            "session_id": sid,
            "started_at": time.time(),
            "last_activity": time.time(),
            "state": VoiceSessionState.IDLE,
            "cost": 0.0
        }
        self._speaking_cancelled[sid] = False
        return sid

    def close_session(self, session_id: str) -> None:
        if session_id in self._active_sessions:
            self._active_sessions[session_id]["state"] = VoiceSessionState.DISCONNECTED
            del self._active_sessions[session_id]
        if session_id in self._speaking_cancelled:
            del self._speaking_cancelled[session_id]

    def transcribe(self, audio_data: bytes, session_id: str) -> STTResult:
        """Fail closed until an actual audio-to-text transport is integrated.

        In particular, NEVER return a hard-coded transcript for user microphone
        data. Use services.simulation for explicitly labeled fixture testing.
        """
        raise NotImplementedError("Gemini Live STT transport is not implemented; no audio was transcribed")

    def speak(self, text: str, session_id: str, on_audio_chunk: Optional[Callable[[bytes], None]] = None) -> AudioOutputEvent:
        """Do not emit silent PCM and falsely claim speech was synthesized."""
        raise NotImplementedError("Gemini Live TTS transport is not implemented; no speech was generated")

    def stop_speaking(self, session_id: str) -> None:
        self._speaking_cancelled[session_id] = True
        if session_id in self._active_sessions:
            self._active_sessions[session_id]["state"] = VoiceSessionState.INTERRUPTED


class OpenAIRealtimeVoiceAdapter(STTProvider, VoiceOutputProvider):
    """
    OpenAI Realtime Voice Adapter.
    Strictly DISABLED_PENDING_AUTH by default.
    Contains clean interfaces for future activation without architectural changes.
    """
    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()
        self.provider_mode = VoiceProviderMode.OPENAI_REALTIME
        self.status = "DISABLED_PENDING_AUTH"

    def is_connected(self) -> bool:
        return False

    def transcribe(self, audio_data: bytes, session_id: str) -> STTResult:
        raise PermissionError(
            "OpenAI Realtime Voice is strictly DISABLED_PENDING_AUTH. "
            "Requires explicit authorization and credentials from Zak."
        )

    def speak(self, text: str, session_id: str, on_audio_chunk: Optional[Callable[[bytes], None]] = None) -> AudioOutputEvent:
        raise PermissionError(
            "OpenAI Realtime Voice is strictly DISABLED_PENDING_AUTH. "
            "Requires explicit authorization and credentials from Zak."
        )

    def stop_speaking(self, session_id: str) -> None:
        pass
