"""HTTP endpoints for the Voice page (speech-to-text, text-to-speech, consent).

The server enforces Host, CSRF, session and permission before these run.
"""
from __future__ import annotations

from services.capabilities.registry import register_state_provider
from ui.routes import SERVICES, Raw, ServiceUnavailable, route

from .cloud import GeminiVoice


def _voice() -> GeminiVoice:
    v = SERVICES.get("voice")
    if v is None:
        router = SERVICES.get("router")
        if router is None:
            raise ServiceUnavailable("Model router is not attached; voice needs it")
        v = SERVICES.setdefault("voice", GeminiVoice(router, SERVICES.get("firewall"),
                                                     SERVICES.get("emergency_stop")))
    return v


def _call(fn, *args):
    from services.model_gateway.cost_controller import BudgetExceededError
    try:
        return fn(*args)
    except BudgetExceededError as exc:
        raise ServiceUnavailable(f"Spending cap reached: {exc}")
    except RuntimeError as exc:
        if type(exc).__name__ == "EmergencyStopActive":
            raise
        raise ServiceUnavailable(str(exc))


@route("GET", r"/api/voice/status", permission="CHAT_INTERACTION")
def status(ctx):
    return _voice().status(ctx.user_id)


@route("POST", r"/api/voice/consent", permission="CHAT_INTERACTION")
def consent(ctx):
    revoke = ctx.payload.get("revoke") is True
    ctx.require_confirm("withdraw consent for cloud audio" if revoke else "send your recordings to Google Gemini")
    _voice().set_consent(ctx.user_id, not revoke)
    return {"consent": not revoke}


@route("POST", r"/api/voice/transcribe", permission="CHAT_INTERACTION")
def transcribe(ctx):
    text = _call(_voice().transcribe, ctx.user_id, ctx.payload.get("audio_b64"), ctx.payload.get("mime_type"))
    return {"transcript": text}


@route("POST", r"/api/voice/speak", permission="CHAT_INTERACTION")
def speak(ctx):
    text = ctx.payload.get("text")
    if not isinstance(text, str):
        raise ValueError("text is required")
    return Raw(body=_call(_voice().speak, text), content_type="audio/wav")


def _capability_state():
    router = SERVICES.get("router")
    if router is None:
        return {"state": "unavailable", "detail": "Model router not attached"}
    s = _voice().status("")
    detail = {"available": "Ready: Gemini speech-to-text and text-to-speech",
              "degraded": "Last voice call failed: " + str(s.get("last_error") or "")[:120]}.get(
        s["state"], str(s.get("last_error") or ""))
    return {"state": s["state"], "detail": detail}


register_state_provider("voice", _capability_state)
