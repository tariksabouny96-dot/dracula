"""ElevenLabs text-to-speech for HOOD (the owner's chosen voice).

The owner sets the API key (encrypted vault), the voice ID(s), the model and a
price per 1,000 characters in Settings > Voice. Calls go through the egress
firewall and the spend caps like every other provider; with no key, no voice
ID or no price on file, HOOD refuses and says why. Speech-to-text stays on
Gemini; only HOOD's spoken voice changes.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

HOST = "api.elevenlabs.io"
SECRET_PROVIDER, SECRET_NAME = "elevenlabs", "api_key"
SECRET_REF = f"SECRET://{SECRET_PROVIDER}/{SECRET_NAME}"
DEFAULT_MODEL = "eleven_multilingual_v2"     # best quality, many languages; eleven_flash_v2_5 = fastest
OUTPUT_FORMAT = "mp3_44100_128"              # the API's default format, available on every plan
VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")
MODEL_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")
TRANSIENT = {429, 500, 502, 503, 504}

DEFAULTS: Dict[str, Any] = {"tts_provider": "gemini", "voice_id": None, "x_voice_id": None,
                            "model_id": DEFAULT_MODEL, "price_per_1k_chars": None}


def settings_file() -> Path:
    return Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")).expanduser() / "voice" / "settings.json"


_lock = threading.Lock()


def load_settings() -> Dict[str, Any]:
    try:
        data = json.loads(settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save_settings(update: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and persist owner voice settings; returns the stored settings."""
    current = load_settings()
    new = dict(current)
    if "tts_provider" in update:
        if update["tts_provider"] not in ("gemini", "elevenlabs"):
            raise ValueError("tts_provider must be 'gemini' or 'elevenlabs'")
        new["tts_provider"] = update["tts_provider"]
    for key in ("voice_id", "x_voice_id"):
        if key in update:
            value = (update[key] or "").strip() or None
            if value is not None and not VOICE_ID_RE.match(value):
                raise ValueError(f"{key} doesn't look like an ElevenLabs voice ID (letters and digits)")
            new[key] = value
    if "model_id" in update:
        value = (update["model_id"] or "").strip() or DEFAULT_MODEL
        if not MODEL_ID_RE.match(value):
            raise ValueError("model_id must look like eleven_multilingual_v2")
        new["model_id"] = value
    if "price_per_1k_chars" in update:
        raw = update["price_per_1k_chars"]
        if raw in (None, ""):
            new["price_per_1k_chars"] = None
        else:
            try:
                price = float(raw)
            except (TypeError, ValueError):
                raise ValueError("price_per_1k_chars must be a number (USD per 1,000 characters)")
            if not 0 <= price <= 10:
                raise ValueError("price_per_1k_chars must be between 0 and 10 USD")
            new["price_per_1k_chars"] = price
    if new["tts_provider"] == "elevenlabs" and not new["voice_id"]:
        raise ValueError("Set the HOOD voice ID before choosing ElevenLabs")
    path = settings_file()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(new, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return new


def synthesize(text: str, *, api_key: str, voice_id: str, model_id: str, firewall=None,
               timeout_sec: int = 60) -> bytes:
    """One ElevenLabs text-to-speech call; returns MP3 bytes or raises with a clear reason."""
    if firewall is not None:
        firewall.enforce(HOST, 443, purpose="voice:elevenlabs")
    url = f"https://{HOST}/v1/text-to-speech/{voice_id}?output_format={OUTPUT_FORMAT}"
    body = json.dumps({"text": text, "model_id": model_id}).encode("utf-8")
    headers = {"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    last = "no response"
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                audio = resp.read(30_000_000)
            if not audio:
                raise RuntimeError("ElevenLabs returned no audio")
            return audio
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read(4000) or b"{}").get("detail", "")
                detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
            except (ValueError, OSError):
                pass
            reason = {401: "the ElevenLabs API key was rejected", 404: "voice ID not found",
                      422: "ElevenLabs rejected the request", 429: "ElevenLabs quota or rate limit reached"}
            last = f"{reason.get(exc.code, f'ElevenLabs HTTP {exc.code}')}{': ' + detail[:200] if detail else ''}"
            if exc.code not in TRANSIENT:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"network error: {type(exc).__name__}"
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"ElevenLabs voice failed ({last})")
