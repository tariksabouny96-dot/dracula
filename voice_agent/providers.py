"""Builds the speech stack for a call from the profile Hood returns. Vendor SDK imports live here only."""
from __future__ import annotations

import base64
import io
import os
import wave
from pathlib import Path
from typing import Any, Dict, Optional

# Languages Hood has validated for spoken use; others are passed through but reported as untested.
LANGUAGE_CODES = {"en": "en-US", "fr": "fr-FR", "ar": "ar-XA", "ar-MA": "ar-XA"}
UNTESTED = {"ar", "ar-MA"}


def secret_env(name: str) -> str:
    """Env var or <NAME>_FILE (a file holding the value)."""
    path = os.environ.get(name + "_FILE", "").strip()
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(name, "").strip()


def google_api_key() -> str:
    return secret_env("GOOGLE_API_KEY") or secret_env("GEMINI_API_KEY")


def build_realtime_model(info: Dict[str, Any], instructions: str):
    """Gemini Live through LiveKit's Google plugin: speech in/out, barge-in handled by the model's VAD."""
    from google.genai import types
    from livekit.plugins import google
    key = google_api_key()
    if not key:
        raise RuntimeError("No Gemini API key for the voice worker (GOOGLE_API_KEY / GEMINI_API_KEY[_FILE])")
    return google.realtime.RealtimeModel(
        model=info.get("model") or "gemini-3.8-live", voice=info.get("voice") or "Puck", api_key=key,
        language=LANGUAGE_CODES.get(info.get("language", "en"), "en-US"), instructions=instructions, temperature=0.4,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        # lets a call outlive the Live API's per-connection limits (resume + sliding context window)
        session_resumption=types.SessionResumptionConfig(),
        context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
    )


def wav_to_pcm(data: bytes):
    """(pcm_bytes, sample_rate, channels) from a WAV file; anything else is refused loudly."""
    if data[:4] != b"RIFF":
        raise ValueError("Hood returned non-WAV audio; the cascade voice mode needs the Gemini WAV voice "
                         "(set Settings > Voice to Gemini, not ElevenLabs/browser)")
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("Expected 16-bit PCM audio")
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()


def build_cascade(hood):
    """STT and TTS through Hood's existing consent-gated, cost-tracked Gemini voice."""
    import asyncio
    from livekit import rtc
    from livekit.agents import stt, tts, utils, APIConnectOptions
    from livekit.agents.types import NOT_GIVEN, DEFAULT_API_CONNECT_OPTIONS

    class HoodSTT(stt.STT):
        def __init__(self):
            super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))

        async def _recognize_impl(self, buffer, *, language=NOT_GIVEN, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            frame = rtc.combine_audio_frames(buffer)
            wav = frame.to_wav_bytes()
            text = await asyncio.to_thread(hood.stt, base64.b64encode(wav).decode("ascii"), "audio/wav")
            lang = language if isinstance(language, str) else "en"
            return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                   alternatives=[stt.SpeechData(language=lang, text=text)])

    class HoodTTS(tts.TTS):
        def __init__(self):
            super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=24000, num_channels=1)

        def synthesize(self, text, *, conn_options=DEFAULT_API_CONNECT_OPTIONS):
            return _Chunked(tts=self, input_text=text, conn_options=conn_options)

    class _Chunked(tts.ChunkedStream):
        async def _run(self, output_emitter):
            res = await asyncio.to_thread(hood.tts, self.input_text)
            pcm, rate, channels = wav_to_pcm(base64.b64decode(res["audio_b64"]))
            output_emitter.initialize(request_id=utils.shortuuid(), sample_rate=rate, num_channels=channels,
                                      mime_type="audio/pcm")
            output_emitter.push(pcm)
            output_emitter.flush()

    return HoodSTT(), HoodTTS()
