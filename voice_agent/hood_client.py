"""Minimal HTTP client for Hood's voice-call agent API. Standard library only (no vendor SDK)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class HoodError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Hood API {status}: {message}")
        self.status, self.message = status, message


@dataclass
class TurnReply:
    speech: str
    end_call: bool = False
    needs_confirmation: List[Dict[str, Any]] = field(default_factory=list)
    mission_id: Optional[str] = None


class HoodClient:
    def __init__(self, base_url: str, agent_token: str, *, timeout: float = 60.0, opener=None):
        self.base = base_url.rstrip("/")
        self._token = agent_token
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _call(self, method: str, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        req = urllib.request.Request(self.base + "/api/voicecall/agent/" + path,
                                     data=None if data is None else json.dumps(data).encode("utf-8"), method=method,
                                     headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read() or b"{}").get("error", "")
            except ValueError:
                msg = ""
            raise HoodError(exc.code, msg or exc.reason) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HoodError(0, f"Hood unreachable ({type(exc).__name__})") from None

    def session(self) -> Dict[str, Any]:
        return self._call("GET", "session")

    def turn(self, text: str) -> TurnReply:
        r = self._call("POST", "turn", {"text": text})
        return TurnReply(r.get("speech", ""), bool(r.get("end_call")), r.get("needs_confirmation") or [], r.get("mission_id"))

    def heartbeat(self, usage: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._call("POST", "heartbeat", {"usage": usage or {}})

    def end(self, reason: str = "hood_hangup") -> Dict[str, Any]:
        return self._call("POST", "end", {"reason": reason})

    def stt(self, audio_b64: str, mime_type: str = "audio/wav") -> str:
        return self._call("POST", "stt", {"audio_b64": audio_b64, "mime_type": mime_type}).get("transcript", "")

    def tts(self, text: str) -> Dict[str, Any]:
        return self._call("POST", "tts", {"text": text})
