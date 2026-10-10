"""
HOOD Lightweight Local Wake Word & VAD Providers
Implements local energy/threshold VAD and keyword matcher with Push-To-Talk fallback.
Operates 100% locally with zero cloud streaming and minimal CPU/memory footprint.
Governed by Master System Specification Section 8 & V0.4 Cost-Aware Voice Principle.
"""

import math
import struct
import time
import threading
from typing import Callable, Optional
from services.voice.contracts import WakeWordProvider, VADProvider


class LightweightLocalVAD(VADProvider):
    """
    Computes RMS (Root Mean Square) energy of raw PCM audio frames.
    Classifies frames as speech when energy crosses threshold.
    Avoids multi-gigabyte neural VAD weights on temporary development laptops.
    """
    def __init__(self, energy_threshold: float = 400.0):
        self.energy_threshold = energy_threshold

    def process_frame(self, audio_frame: bytes) -> bool:
        if not audio_frame or len(audio_frame) < 2:
            return False
        
        # Calculate RMS of 16-bit signed PCM samples
        count = len(audio_frame) // 2
        shorts = struct.unpack(f"<{count}h", audio_frame[:count * 2])
        sum_squares = sum(s * s for s in shorts)
        rms = math.sqrt(sum_squares / count) if count > 0 else 0.0
        return rms >= self.energy_threshold


class LocalWakeWordDetector(WakeWordProvider):
    """
    Lightweight local wake word detector.
    Supports continuous keyword monitoring ("Hood") or programmatic / Push-To-Talk trigger.
    Runs entirely on-device; never streams idle background audio to cloud providers.
    """
    def __init__(self, wake_phrase: str = "Hood"):
        self.wake_phrase = wake_phrase.lower()
        self._listening = False
        self._callback: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()

    def start_listening(self, on_wake_detected: Callable[[], None]) -> None:
        with self._lock:
            self._listening = True
            self._callback = on_wake_detected

    def stop_listening(self) -> None:
        with self._lock:
            self._listening = False
            self._callback = None

    def is_listening(self) -> bool:
        with self._lock:
            return self._listening

    def trigger_wake(self) -> bool:
        """Manual / programmatic trigger (e.g. from Push-To-Talk button or local hotkey)."""
        cb = None
        with self._lock:
            if self._listening and self._callback:
                cb = self._callback
        if cb:
            cb()
            return True
        return False

    def process_text_candidate(self, candidate_text: str) -> bool:
        """Checks if transcribed text or phonetic string begins with wake phrase."""
        if not candidate_text:
            return False
        clean = candidate_text.strip().lower()
        if clean.startswith(self.wake_phrase) or self.wake_phrase in clean:
            self.trigger_wake()
            return True
        return False
