"""
HOOD Voice Subsystem - Contracts & Base Abstractions
Provider-neutral interfaces for Wake Word, VAD, STT, Voice Output, and Realtime Audio sessions.
Governed by Master System Specification Sections 2, 8, 14 & V0.4 Jarvis Voice Spec.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import uuid


class VoiceProviderMode(str, Enum):
    LOCAL = "LOCAL"
    GEMINI_LIVE = "GEMINI_LIVE"
    OPENAI_REALTIME = "OPENAI_REALTIME"
    FUTURE_PROVIDER = "FUTURE_PROVIDER"


class VoiceSessionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"


class MicrophonePrivacyState(str, Enum):
    MUTED = "MUTED"
    HARDWARE_OFF = "HARDWARE_OFF"
    LOCAL_WAKE_ACTIVE = "LOCAL_WAKE_ACTIVE"
    STREAMING_TO_PROVIDER = "STREAMING_TO_PROVIDER"


class AudioInputEvent(BaseModel):
    session_id: str
    audio_bytes: Optional[bytes] = None
    sample_rate: int = 16000
    is_speech: bool = True
    transcript_preview: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AudioOutputEvent(BaseModel):
    session_id: str
    text_content: str
    audio_stream_url: Optional[str] = None
    audio_format: str = "pcm_16000"
    is_complete: bool = False
    cancelled: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class STTResult(BaseModel):
    transcript: str
    confidence: float = 1.0
    session_id: str
    latency_ms: int = 0
    provider: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class VoiceSessionMetrics(BaseModel):
    session_id: str
    provider: VoiceProviderMode
    start_time: str
    end_time: Optional[str] = None
    active_duration_seconds: float = 0.0
    audio_input_duration_seconds: float = 0.0
    audio_output_duration_seconds: float = 0.0
    model_name: Optional[str] = None
    estimated_cost_usd: float = 0.0
    is_free_tier: bool = True


# =========================================================================
# PROVIDER INTERFACES
# =========================================================================

class WakeWordProvider(ABC):
    """Local wake detection abstraction preventing continuous cloud audio transmission."""
    @abstractmethod
    def start_listening(self, on_wake_detected: Callable[[], None]) -> None:
        pass

    @abstractmethod
    def stop_listening(self) -> None:
        pass

    @abstractmethod
    def is_listening(self) -> bool:
        pass


class VADProvider(ABC):
    """Voice Activity Detection abstraction determining speech start and end boundaries."""
    @abstractmethod
    def process_frame(self, audio_frame: bytes) -> bool:
        """Returns True if speech is detected in the audio frame."""
        pass


class STTProvider(ABC):
    """Provider-neutral Speech-to-Text conversion."""
    @abstractmethod
    def transcribe(self, audio_data: bytes, session_id: str) -> STTResult:
        pass


class VoiceOutputProvider(ABC):
    """Provider-neutral text-to-speech and audio streaming output."""
    @abstractmethod
    def speak(self, text: str, session_id: str, on_audio_chunk: Optional[Callable[[bytes], None]] = None) -> AudioOutputEvent:
        pass

    @abstractmethod
    def stop_speaking(self, session_id: str) -> None:
        """Interrupts / halts ongoing speech output immediately."""
        pass
