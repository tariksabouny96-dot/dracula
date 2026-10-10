"""Real speech for the HOOD console: Gemini speech-to-text and text-to-speech.

Every call is governed like a chat call: the owner's Gemini key (Settings or
environment), the egress firewall (default deny), a price on file (no
unknown-cost calls), the spend caps, and the emergency stop. Microphone audio is
only sent after the user's explicit, recorded consent; text-to-speech sends only
text. Nothing here pretends: with no key, no price or no consent it refuses and
says why.
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from packages.contracts import ModelUsage, ProviderName

from . import elevenlabs as eleven

HOST = "generativelanguage.googleapis.com"
AUDIO_MIME = {"audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/mpeg", "audio/aac", "audio/flac"}
MAX_AUDIO_BYTES = 700 * 1024            # fits the server's 1 MB request cap; ~3 min of browser speech
MAX_SPEAK_CHARS = 2000
TRANSIENT = {429, 500, 502, 503, 504}


class VoiceRefused(ValueError):
    """A precondition (consent, key, price, input) is not met; nothing was sent."""


def stt_model() -> str:
    return os.environ.get("HOOD_GEMINI_STT_MODEL", "gemini-3.5-flash-lite")


def tts_model() -> str:
    return os.environ.get("HOOD_GEMINI_TTS_MODEL", "gemini-3.8-flash-tts")


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class GeminiVoice:
    def __init__(self, router, firewall=None, emergency_stop=None, data_dir: Optional[Path] = None,
                 voice_name: str = "Kore"):
        self.router = router
        self.firewall = firewall
        self.emergency_stop = emergency_stop
        self.voice_name = os.environ.get("HOOD_GEMINI_VOICE", voice_name)
        base = Path(data_dir) if data_dir else Path(
            os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "voice"
        base.mkdir(parents=True, exist_ok=True)
        self.consent_file = base / "consent.json"
        self._lock = threading.Lock()
        self.last_error: Optional[str] = None

    # ----------------------------------------------------------------- consent
    def _consents(self) -> Dict[str, Any]:
        try:
            return json.loads(self.consent_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def has_consent(self, user_id: str) -> bool:
        return bool(self._consents().get(user_id))

    def set_consent(self, user_id: str, given: bool) -> None:
        with self._lock:
            data = self._consents()
            if given:
                data[user_id] = {"given_at": datetime.now(timezone.utc).isoformat()}
            else:
                data.pop(user_id, None)
            tmp = self.consent_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.consent_file)

    # ----------------------------------------------------------------- status
    def _gemini(self):
        return self.router.providers.get(ProviderName.GEMINI) if self.router is not None else None

    def _price(self, model: str):
        return (getattr(self.router, "price_table", {}) or {}).get("gemini", {}).get(model)

    def _elevenlabs_problem(self, cfg: Dict[str, Any]) -> Optional[str]:
        """Why ElevenLabs can't be used right now, or None when it is ready."""
        g = self._gemini()
        if g is None or not g.vault.get_secret(eleven.SECRET_REF):
            return "No ElevenLabs API key: set it in Settings > Voice"
        if not cfg.get("voice_id"):
            return "No ElevenLabs voice ID: set it in Settings > Voice"
        if cfg.get("price_per_1k_chars") is None:
            return ("No ElevenLabs price on file: set USD per 1,000 characters in Settings > Voice "
                    "(0 if your plan covers it)")
        return None

    def status(self, user_id: str) -> Dict[str, Any]:
        g = self._gemini()
        configured = bool(g and g.enabled and g.is_healthy())
        cfg = eleven.load_settings()
        use_eleven = cfg["tts_provider"] == "elevenlabs"
        needed = (stt_model(),) if use_eleven else (stt_model(), tts_model())
        missing = [m for m in needed if self._price(m) is None]
        eleven_problem = self._elevenlabs_problem(cfg) if use_eleven else None
        if not configured or missing or eleven_problem:
            state = "not_configured"
        elif self.last_error:
            state = "degraded"
        else:
            state = "available"
        provider = (f"ElevenLabs voice {cfg['voice_id']} ({cfg['model_id']}) to speak; Google Gemini to listen"
                    if use_eleven else "Google Gemini (speech-to-text + text-to-speech)")
        if not configured:
            problem = "No Gemini API key: set it in Settings"
        elif missing:
            problem = "No price on file for " + ", ".join(missing) + ": set prices in Settings"
        else:
            problem = eleven_problem
        return {"state": state, "provider": provider, "tts_provider": cfg["tts_provider"],
                "consent": self.has_consent(user_id), "stt_model": stt_model(),
                "tts_model": cfg["model_id"] if use_eleven else tts_model(),
                "last_error": problem or self.last_error}

    # ----------------------------------------------------------------- calls
    def _preflight(self, model: str):
        if self.emergency_stop is not None and getattr(self.emergency_stop, "is_active", False):
            from packages.security import EmergencyStopActive
            raise EmergencyStopActive("Emergency stop is engaged; voice is halted")
        g = self._gemini()
        if not (g and g.enabled and g.is_healthy()):
            raise VoiceRefused("No Gemini API key: set it in Settings")
        price = self._price(model)
        if price is None:
            raise VoiceRefused(f"No price on file for gemini/{model}; HOOD refuses unknown-cost calls (Settings)")
        if self.firewall is not None:
            self.firewall.enforce(HOST, 443, purpose=f"voice:{model}")
        return g, price

    def _post(self, g, model: str, body: Dict[str, Any], est_in: int, est_out: int, price) -> Dict[str, Any]:
        cc = self.router.cost_controller
        estimate = est_in / 1000.0 * price.input_per_1k_usd + est_out / 1000.0 * price.output_per_1k_usd
        reservation = cc.reserve(None, estimate)
        headers = {"Content-Type": "application/json"}
        key = g._get_api_key()
        if key:
            headers["x-goog-api-key"] = key
        url = f"https://{HOST}/v1beta/models/{model}:generateContent"
        started = time.monotonic()
        data, last = None, None
        for attempt in range(3):
            req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read(40_000_000))
                break
            except urllib.error.HTTPError as exc:
                last = f"Gemini HTTP {exc.code}"
                if exc.code not in TRANSIENT:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = f"network error: {type(exc).__name__}"
            time.sleep(1.5 * (attempt + 1))
        if data is None:
            # The request may have been processed: charge the reservation, never release it.
            cc.settle(reservation, ModelUsage(), cost_measured=False, provider="gemini", model=model)
            self.last_error = last
            raise RuntimeError(f"Voice call failed ({last})")
        meta = data.get("usageMetadata") or {}
        usage = ModelUsage(prompt_tokens=int(meta.get("promptTokenCount", 0)),
                           completion_tokens=int(meta.get("candidatesTokenCount", 0)),
                           total_tokens=int(meta.get("totalTokenCount", 0)))
        measured = usage.total_tokens > 0
        if measured:
            usage.estimated_cost_usd = (usage.prompt_tokens / 1000.0 * price.input_per_1k_usd +
                                        usage.completion_tokens / 1000.0 * price.output_per_1k_usd)
        cc.settle(reservation, usage, cost_measured=measured, provider="gemini", model=model,
                  latency_ms=int((time.monotonic() - started) * 1000),
                  price_source=f"{price.source} ({price.as_of})")
        self.last_error = None
        return data

    def transcribe(self, user_id: str, audio_b64: str, mime_type: str) -> str:
        if not self.has_consent(user_id):
            raise VoiceRefused("Give consent for cloud audio first (Voice page)")
        mime = (mime_type or "").split(";")[0].strip().lower()
        if mime not in AUDIO_MIME:
            raise VoiceRefused(f"Unsupported audio type {mime_type!r}")
        try:
            audio = base64.b64decode(audio_b64 or "", validate=True)
        except ValueError:
            raise VoiceRefused("audio_b64 is not valid base64")
        if not audio:
            raise VoiceRefused("No audio received")
        if len(audio) > MAX_AUDIO_BYTES:
            raise VoiceRefused("Recording too long; keep it under about 3 minutes")
        model = stt_model()
        g, price = self._preflight(model)
        body = {"contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode("ascii")}},
            {"text": "Transcribe this audio verbatim in its original language. "
                     "Reply with only the transcript, or nothing if there is no speech."}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 2048}}
        data = self._post(g, model, body, est_in=max(1000, len(audio) // 50), est_out=2048, price=price)
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()

    def speak_audio(self, text: str, speaker: str = "hood") -> tuple:
        """Speak with the owner's chosen provider; returns (audio bytes, mime type)."""
        cfg = eleven.load_settings()
        if cfg["tts_provider"] != "elevenlabs":
            return self.speak(text), "audio/wav"
        text = (text or "").strip()
        if not text:
            raise VoiceRefused("Nothing to say")
        if len(text) > MAX_SPEAK_CHARS:
            raise VoiceRefused(f"Text too long (max {MAX_SPEAK_CHARS} characters)")
        if self.emergency_stop is not None and getattr(self.emergency_stop, "is_active", False):
            from packages.security import EmergencyStopActive
            raise EmergencyStopActive("Emergency stop is engaged; voice is halted")
        problem = self._elevenlabs_problem(cfg)
        if problem:
            raise VoiceRefused(problem)
        voice_id = (cfg.get("x_voice_id") if speaker == "x" else None) or cfg["voice_id"]
        cost = len(text) / 1000.0 * float(cfg["price_per_1k_chars"])
        cc = self.router.cost_controller
        reservation = cc.reserve(None, cost)
        started = time.monotonic()
        try:
            audio = eleven.synthesize(text, api_key=self._gemini().vault.get_secret(eleven.SECRET_REF),
                                      voice_id=voice_id, model_id=cfg["model_id"], firewall=self.firewall)
        except Exception as exc:
            # The request may have been processed: charge the reservation, never release it.
            cc.settle(reservation, ModelUsage(), cost_measured=False, provider="elevenlabs", model=cfg["model_id"])
            self.last_error = str(exc)[:300]
            raise
        cc.settle(reservation, ModelUsage(estimated_cost_usd=cost),  # billed per character, not per token
                  cost_measured=True, provider="elevenlabs", model=cfg["model_id"],
                  latency_ms=int((time.monotonic() - started) * 1000),
                  price_source=f"owner-declared ${cfg['price_per_1k_chars']}/1k characters (Settings)")
        self.last_error = None
        return audio, "audio/mpeg"

    def speak(self, text: str) -> bytes:
        text = (text or "").strip()
        if not text:
            raise VoiceRefused("Nothing to say")
        if len(text) > MAX_SPEAK_CHARS:
            raise VoiceRefused(f"Text too long (max {MAX_SPEAK_CHARS} characters)")
        model = tts_model()
        g, price = self._preflight(model)
        body = {"contents": [{"parts": [{"text": text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice_name}}}}}
        data = self._post(g, model, body, est_in=len(text), est_out=max(512, len(text) * 4), price=price)
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        inline = next((p.get("inlineData") or p.get("inline_data") for p in parts
                       if p.get("inlineData") or p.get("inline_data")), None)
        if not inline:
            self.last_error = "Gemini returned no audio"
            raise RuntimeError("Gemini returned no audio")
        raw = base64.b64decode(inline["data"])
        mime = inline.get("mimeType", inline.get("mime_type", "")).lower()
        if mime.startswith("audio/wav") or raw[:4] == b"RIFF":
            return raw
        rate = 24000
        for part in mime.replace(" ", "").split(";"):
            if part.startswith("rate="):
                rate = int(part[5:])
        return pcm_to_wav(raw, rate)
