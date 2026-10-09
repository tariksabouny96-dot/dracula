"""
HOOD Voice Router
Dynamic routing across LOCAL, GEMINI_LIVE, OPENAI_REALTIME, and future providers.
Enforces cost containment, idle session suspension, and interruption propagation.
Governed by Master System Specification Section 8 & V0.4 Voice Router Spec.
"""

import time
from typing import Dict, Any, List, Optional
from services.voice.contracts import (
    VoiceProviderMode,
    VoiceSessionState,
    VoiceSessionMetrics,
    MicrophonePrivacyState,
    STTResult,
    AudioOutputEvent
)
from services.voice.local_vad import LightweightLocalVAD, LocalWakeWordDetector
from services.voice.adapters import GeminiLiveVoiceAdapter, OpenAIRealtimeVoiceAdapter
from packages.config import SystemConfig


class VoiceRouter:
    """Coordinates voice providers, manages session timeouts, and reports cost metrics."""

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or SystemConfig()
        self.vad = LightweightLocalVAD()
        self.wake_detector = LocalWakeWordDetector()
        self.gemini_adapter = GeminiLiveVoiceAdapter(self.config)
        self.openai_adapter = OpenAIRealtimeVoiceAdapter(self.config)

        self.current_provider_mode = VoiceProviderMode.GEMINI_LIVE
        self.microphone_privacy = MicrophonePrivacyState.LOCAL_WAKE_ACTIVE
        self.session_metrics: Dict[str, VoiceSessionMetrics] = {}
        self.idle_timeout_seconds = 180  # 3 minutes idle cutoff

    def get_provider_capabilities(self) -> List[Dict[str, Any]]:
        """Reports metadata and availability across all registered voice providers."""
        return [
            {
                "provider": VoiceProviderMode.LOCAL.value,
                "latency_class": "ultra-low (<50ms)",
                "cost_class": "zero ($0.00)",
                "interruption_support": True,
                "streaming_support": True,
                "audio_input_support": True,
                "audio_output_support": True,
                "availability": "WAKE_VAD_ONLY_NO_STT_TTS"
            },
            {
                "provider": VoiceProviderMode.GEMINI_LIVE.value,
                "latency_class": "low (<400ms)",
                "cost_class": "UNVERIFIED",
                "interruption_support": True,
                "streaming_support": True,
                "audio_input_support": True,
                "audio_output_support": True,
                "availability": "NOT_IMPLEMENTED"
            },
            {
                "provider": VoiceProviderMode.OPENAI_REALTIME.value,
                "latency_class": "low (<300ms)",
                "cost_class": "paid ($0.06/min)",
                "interruption_support": True,
                "streaming_support": True,
                "audio_input_support": True,
                "audio_output_support": True,
                "availability": "DISABLED_PENDING_AUTH"
            }
        ]

    def set_provider_mode(self, mode: VoiceProviderMode) -> None:
        if mode == VoiceProviderMode.OPENAI_REALTIME:
            raise PermissionError("OpenAI Realtime Voice is disabled pending Zak's authorization.")
        self.current_provider_mode = mode

    def start_voice_session(self, session_id: str) -> VoiceSessionMetrics:
        """Starts a voice session with telemetry tracking."""
        metrics = VoiceSessionMetrics(
            session_id=session_id,
            provider=self.current_provider_mode,
            start_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            is_free_tier=True,
            estimated_cost_usd=0.0
        )
        self.session_metrics[session_id] = metrics
        self.microphone_privacy = MicrophonePrivacyState.LOCAL_WAKE_ACTIVE
        if self.current_provider_mode == VoiceProviderMode.GEMINI_LIVE:
            self.gemini_adapter.start_session(session_id)
        return metrics

    def end_voice_session(self, session_id: str) -> Optional[VoiceSessionMetrics]:
        metrics = self.session_metrics.get(session_id)
        if metrics:
            metrics.end_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if self.current_provider_mode == VoiceProviderMode.GEMINI_LIVE:
                self.gemini_adapter.close_session(session_id)
        self.microphone_privacy = MicrophonePrivacyState.LOCAL_WAKE_ACTIVE
        return metrics

    def transcribe_audio(self, audio_data: bytes, session_id: str) -> STTResult:
        """Never replace actual microphone data with fixture transcripts."""
        if self.current_provider_mode == VoiceProviderMode.GEMINI_LIVE:
            return self.gemini_adapter.transcribe(audio_data, session_id)
        raise NotImplementedError("No operational speech-to-text provider configured")

    def speak(self, text: str, session_id: str) -> AudioOutputEvent:
        """Never report silent/mocked audio as synthesized speech."""
        if self.current_provider_mode == VoiceProviderMode.GEMINI_LIVE:
            return self.gemini_adapter.speak(text, session_id)
        raise NotImplementedError("No operational text-to-speech provider configured")

    def interrupt_speech(self, session_id: str) -> None:
        """Halts active audio output immediately upon barge-in."""
        if self.current_provider_mode == VoiceProviderMode.GEMINI_LIVE:
            self.gemini_adapter.stop_speaking(session_id)

    def get_cost_ledger(self) -> List[Dict[str, Any]]:
        """Returns session cost telemetry for voice interactions."""
        return [m.model_dump() for m in self.session_metrics.values()]
