"""Usage metering for the realtime model: what the call actually consumed, reported to Hood's ledger."""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

AUDIO_TOKENS_PER_SECOND = 25      # Google: 25 tokens per second of audio


class UsageMeter:
    """Accumulates measured tokens (from the model's metrics) and, as a fallback, wall-clock speaking time.

    ``drain()`` returns what changed since the last call so each heartbeat reports a delta.
    """

    def __init__(self, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._d = {"audio_in_tokens": 0, "audio_out_tokens": 0, "text_in_tokens": 0, "text_out_tokens": 0}
        self._speaking_since: Optional[float] = None
        self._listen_since: Optional[float] = self._clock()
        self._fallback = {"audio_out_s": 0.0, "audio_in_s": 0.0}
        self._measured = False
        self.unbridged_turns = 0

    # ------------------------------------------------------------------ inputs
    def on_metrics(self, m: Any) -> None:
        """Accept a livekit RealtimeModelMetrics (duck-typed so tests need no SDK)."""
        if getattr(m, "type", "") != "realtime_model_metrics":
            return
        inp, out = getattr(m, "input_token_details", None), getattr(m, "output_token_details", None)
        with self._lock:
            self._measured = True
            self._d["audio_in_tokens"] += int(getattr(inp, "audio_tokens", 0) or 0)
            self._d["text_in_tokens"] += int(getattr(inp, "text_tokens", 0) or 0)
            self._d["audio_out_tokens"] += int(getattr(out, "audio_tokens", 0) or 0)
            self._d["text_out_tokens"] += int(getattr(out, "text_tokens", 0) or 0)

    def agent_state(self, state: str) -> None:
        now = self._clock()
        with self._lock:
            if state == "speaking" and self._speaking_since is None:
                self._speaking_since = now
            elif state != "speaking" and self._speaking_since is not None:
                self._fallback["audio_out_s"] += now - self._speaking_since
                self._speaking_since = None

    def unbridged(self) -> None:
        with self._lock:
            self.unbridged_turns += 1

    # ------------------------------------------------------------------ output
    def drain(self) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            out: Dict[str, Any] = {}
            if self._measured:
                out["audio_in_s"] = self._d["audio_in_tokens"] / AUDIO_TOKENS_PER_SECOND
                out["audio_out_s"] = self._d["audio_out_tokens"] / AUDIO_TOKENS_PER_SECOND
                out["text_in_tokens"], out["text_out_tokens"] = self._d["text_in_tokens"], self._d["text_out_tokens"]
                self._d = {k: 0 for k in self._d}
            else:                                    # no metrics yet: wall-clock estimate, flagged by omission of tokens
                speaking = self._fallback["audio_out_s"] + ((now - self._speaking_since) if self._speaking_since else 0.0)
                listening = (now - self._listen_since) if self._listen_since is not None else 0.0
                out["audio_in_s"], out["audio_out_s"] = listening, speaking
                self._listen_since = now
                self._fallback["audio_out_s"] = 0.0
                if self._speaking_since is not None:
                    self._speaking_since = now
            out["unbridged_turns"] = self.unbridged_turns
            return out
