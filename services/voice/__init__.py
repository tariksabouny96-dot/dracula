"""
HOOD Voice Subsystem Package
Governed by Master System Specification Section 8 & V0.4 Voice Router Spec.
"""

from .contracts import (
    VoiceProviderMode,
    VoiceSessionState,
    MicrophonePrivacyState,
    AudioInputEvent,
    AudioOutputEvent,
    STTResult,
    VoiceSessionMetrics,
    WakeWordProvider,
    VADProvider,
    STTProvider,
    VoiceOutputProvider
)
from .local_vad import LightweightLocalVAD, LocalWakeWordDetector
from .adapters import GeminiLiveVoiceAdapter, OpenAIRealtimeVoiceAdapter
from .voice_router import VoiceRouter

__all__ = [
    "VoiceProviderMode",
    "VoiceSessionState",
    "MicrophonePrivacyState",
    "AudioInputEvent",
    "AudioOutputEvent",
    "STTResult",
    "VoiceSessionMetrics",
    "WakeWordProvider",
    "VADProvider",
    "STTProvider",
    "VoiceOutputProvider",
    "LightweightLocalVAD",
    "LocalWakeWordDetector",
    "GeminiLiveVoiceAdapter",
    "OpenAIRealtimeVoiceAdapter",
    "VoiceRouter"
]
