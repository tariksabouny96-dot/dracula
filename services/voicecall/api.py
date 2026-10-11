"""HTTP routes for voice calling (registered through ui/routes.py).

Three audiences, three credentials:
- owner console (Hood session, owner-only): status, settings, device registration, history, test events;
- the owner's iPhone (``Authorization: HoodDevice <id>.<secret>``): start/answer/decline/end calls,
  push tokens, confirmations (signed with the device's biometric key), home data;
- the voice worker (``Authorization: Bearer <per-call agent token>``): fetch the call, post turns,
  heartbeats with usage. A worker can only act on the one call its token names.
The server (ui/server.py) authenticates and rate-limits before any handler runs.
"""
from __future__ import annotations

from typing import Any, Dict

from ui.routes import SERVICES, ServiceUnavailable, route

from .contracts import EndReason, EventSeverity, HoodEvent, NotFound, VoiceError

CALL = r"/api/voicecall/calls/(?P<call_id>call_[0-9a-f]{20})"
OWNER = "X_ACTIVATION"           # same owner-only permission the console chat uses


def _svc():
    svc = SERVICES.get("voicecall")
    if svc is None:
        raise ServiceUnavailable("Voice calling is not configured on this server")
    return svc


def _need_confirm(ctx, what: str) -> None:
    ctx.require_confirm(what)


def _speech(svc):
    """Hood's existing consent-gated, cost-tracked Gemini speech (used by the cascade mode)."""
    if svc.speech is not None:
        return svc.speech
    from services.voice.api import _voice
    return _voice()


# =========================================================================== owner console
@route("GET", r"/api/voicecall/status", permission=OWNER)
def status(ctx):
    return _svc().status(ctx.user_id)


@route("GET", r"/api/voicecall/history", permission=OWNER)
def history(ctx):
    s = _svc()
    return {"calls": [c.public() for c in s.store.list_calls(ctx.user_id, 50)], "events": s.store.recent_events(30)}


@route("GET", r"/api/voicecall/costs", permission=OWNER)
def costs(ctx):
    s = _svc()
    return {"this_month": s.cost.month_table(ctx.user_id), "scenarios_usd": s.cost_scenarios(),
            "note": "Provider prices are quotations with a source and date (see provider.price_source in /status); "
                    "scenario figures are estimates assuming the owner speaks half the time."}


@route("GET", r"/api/voicecall/settings", permission=OWNER)
def get_settings(ctx):
    from .policy import effective_settings
    s = _svc()
    return effective_settings(s.store, s.cfg, ctx.user_id)


@route("POST", r"/api/voicecall/settings", permission=OWNER)
def put_settings(ctx):
    from .policy import update_settings
    s = _svc()
    patch = ctx.payload.get("settings")
    if not isinstance(patch, dict):
        raise ValueError("settings object required")
    return update_settings(s.store, s.cfg, ctx.user_id, patch)


@route("GET", r"/api/voicecall/devices", permission=OWNER)
def list_devices(ctx):
    return {"devices": _svc().devices.list(ctx.user_id)}


@route("POST", r"/api/voicecall/devices/register", permission=OWNER)
def register_device(ctx):
    _need_confirm(ctx, "register this phone to call you and to confirm decisions")
    s = _svc()
    s.perms.require_owner(ctx.user_id)
    res = s.devices.register(ctx.user_id, ctx.payload.get("name", ""), ctx.payload.get("public_key", ""),
                             platform=str(ctx.payload.get("platform", "ios")), app_version=str(ctx.payload.get("app_version", "")))
    res["note"] = "The device secret is shown once. Store it in the iOS Keychain; Hood keeps only its digest."
    return res


@route("POST", r"/api/voicecall/devices/revoke", permission=OWNER)
def revoke_device(ctx):
    _need_confirm(ctx, "revoke this device")
    s = _svc()
    did = str(ctx.payload.get("device_id", ""))
    ok = s.devices.revoke(ctx.user_id, did)
    if ok:
        for c in s.store.active_calls(ctx.user_id):
            if c.device_id == did:
                s.sessions.end_call(c.call_id, EndReason.REVOKED, actor="owner")
    return {"revoked": ok}


@route("POST", r"/api/voicecall/test-event", permission=OWNER)
def test_event(ctx):
    """Owner-triggered test of the Hood-initiated path (rings the phone if policy allows)."""
    _need_confirm(ctx, "let Hood ring or notify your registered phone as a test")
    s = _svc()
    kind = ctx.payload.get("event_type", "decision_blocking")
    sev = EventSeverity(ctx.payload.get("severity", "high"))
    ev = HoodEvent(type=kind, severity=sev, subject="test:" + str(ctx.payload.get("subject", "manual")),
                   summary=str(ctx.payload.get("summary", "This is a test call from Hood"))[:160],
                   detail=str(ctx.payload.get("detail", "You asked me to test calling you."))[:400], source="owner-test")
    return s.emit_event(ev, ctx.user_id)


@route("POST", r"/api/voicecall/calls/end-active", permission=OWNER)
def end_active(ctx):
    s = _svc()
    ended = [s.sessions.end_call(c.call_id, EndReason.OWNER_HANGUP, actor="console").call_id
             for c in s.store.active_calls(ctx.user_id)]
    return {"ended": ended}


# =========================================================================== the owner's phone
def _dev(ctx) -> Dict[str, Any]:
    return ctx.principal


@route("GET", r"/api/voicecall/device/home", permission=OWNER, auth="device")
def device_home(ctx):
    s, d = _svc(), _dev(ctx)
    owner = d["owner_id"]
    eng = SERVICES.get("agents")
    missions = []
    if eng is not None:
        missions = [{"id": m["id"], "state": m["state"], "objective": (m["objective"] or "")[:120]}
                    for m in eng.list(owner) if m["state"] not in ("COMPLETED", "FAILED", "UNVERIFIED", "CANCELLED")][:10]
    return {"hood": {"voice_enabled": s.cfg.enabled, "transport_ready": s.transport is not None},
            "active_missions": missions, "notifications": s.store.recent_events(15),
            "pending_confirmations": len(s.confirm.pending(owner)), "spend": s.cost.month_table(owner),
            "active_call": next((c.public() for c in s.store.active_calls(owner)), None)}


@route("POST", r"/api/voicecall/device/push-token", permission=OWNER, auth="device")
def device_push_token(ctx):
    p = ctx.payload
    _svc().devices.set_push_tokens(_dev(ctx)["device_id"], voip_token=p.get("voip_token"), alert_token=p.get("alert_token"),
                                   apns_env=p.get("apns_env"))
    return {"ok": True}


@route("GET", r"/api/voicecall/device/settings", permission=OWNER, auth="device")
def device_get_settings(ctx):
    from .policy import effective_settings
    s = _svc()
    return effective_settings(s.store, s.cfg, _dev(ctx)["owner_id"])


@route("POST", r"/api/voicecall/device/settings", permission=OWNER, auth="device")
def device_put_settings(ctx):
    from .policy import update_settings
    s = _svc()
    patch = ctx.payload.get("settings")
    if not isinstance(patch, dict):
        raise ValueError("settings object required")
    return update_settings(s.store, s.cfg, _dev(ctx)["owner_id"], patch)


@route("POST", r"/api/voicecall/calls/start", permission=OWNER, auth="device")
def call_start(ctx):
    d = _dev(ctx)
    return _svc().sessions.start_owner_call(d["owner_id"], d, ctx.payload.get("language"))


@route("POST", r"/api/voicecall/calls/callback", permission=OWNER, auth="device")
def call_callback(ctx):
    d = _dev(ctx)
    return _svc().dispatcher.callback(d["owner_id"], d, str(ctx.payload.get("call_id", "")))


@route("POST", CALL + r"/answer", permission=OWNER, auth="device")
def call_answer(ctx):
    return _svc().sessions.answer(ctx.match["call_id"], _dev(ctx)["device_id"])


@route("POST", CALL + r"/decline", permission=OWNER, auth="device")
def call_decline(ctx):
    return _svc().sessions.decline(ctx.match["call_id"], _dev(ctx)["device_id"])


@route("POST", CALL + r"/rejoin", permission=OWNER, auth="device")
def call_rejoin(ctx):
    return _svc().sessions.rejoin(ctx.match["call_id"], _dev(ctx)["device_id"])


@route("POST", CALL + r"/connected", permission=OWNER, auth="device")
def call_connected(ctx):
    return _svc().sessions.confirm_connected(ctx.match["call_id"], _dev(ctx)["device_id"])


@route("POST", CALL + r"/end", permission=OWNER, auth="device")
def call_end(ctx):
    s = _svc()
    call = s.sessions._owned(ctx.match["call_id"], _dev(ctx)["device_id"])
    return {"call": s.sessions.end_call(call.call_id, EndReason.OWNER_HANGUP, actor="device").public()}


@route("GET", CALL, permission=OWNER, auth="device")
def call_get(ctx):
    s = _svc()
    call = s.sessions._owned(ctx.match["call_id"], _dev(ctx)["device_id"])
    after = int((ctx.query.get("after") or ["0"])[0] or 0)
    return {"call": call.public(), "events": s.store.events_after(call.call_id, after)}


@route("GET", r"/api/voicecall/confirmations", permission=OWNER, auth="device")
def conf_pending(ctx):
    return {"items": _svc().confirm.pending(_dev(ctx)["owner_id"])}


@route("POST", r"/api/voicecall/confirmations/challenge", permission=OWNER, auth="device")
def conf_challenge(ctx):
    d = _dev(ctx)
    p = ctx.payload
    return _svc().confirm.issue_challenge(d["owner_id"], d, str(p.get("kind", "")), str(p.get("subject", "")),
                                          str(p.get("decision", "")))


@route("POST", r"/api/voicecall/confirmations/submit", permission=OWNER, auth="device")
def conf_submit(ctx):
    d = _dev(ctx)
    p = ctx.payload
    return _svc().confirm.submit(d["owner_id"], d, str(p.get("challenge_id", "")), str(p.get("signature", "")))


# =========================================================================== the voice worker
def _call(ctx):
    return ctx.principal


@route("GET", r"/api/voicecall/agent/session", permission=OWNER, auth="agent")
def agent_session(ctx):
    s, c = _svc(), _call(ctx)
    prof = s.sessions.profile()
    st = s.store.get_call(c.call_id)
    return {"call_id": c.call_id, "state": st.state.value, "direction": st.direction.value, "language": st.language,
            "mode": prof.mode, "model": prof.model, "voice": prof.voice, "briefing": st.briefing,
            "limits": s.cost.limits_for(st.owner_id), "max_utterance_chars": 1000}


@route("POST", r"/api/voicecall/agent/turn", permission=OWNER, auth="agent")
def agent_turn(ctx):
    s = _svc()
    text = ctx.payload.get("text")
    if not isinstance(text, str):
        raise ValueError("text is required")
    call = s.store.get_call(_call(ctx).call_id)
    r = s.bridge.handle_turn(call, text)
    return {"speech": r.speech, "end_call": r.end_call, "needs_confirmation": r.needs_confirmation,
            "mission_id": r.mission_id, "reason": r.reason}


@route("POST", r"/api/voicecall/agent/heartbeat", permission=OWNER, auth="agent")
def agent_heartbeat(ctx):
    s = _svc()
    usage = ctx.payload.get("usage")
    return s.sessions.agent_heartbeat(_call(ctx), usage if isinstance(usage, dict) else None)


@route("POST", r"/api/voicecall/agent/end", permission=OWNER, auth="agent")
def agent_end(ctx):
    s = _svc()
    try:
        reason = EndReason(str(ctx.payload.get("reason", EndReason.HOOD_HANGUP.value)))
    except ValueError:
        reason = EndReason.HOOD_HANGUP
    return {"call": s.sessions.end_call(_call(ctx).call_id, reason, actor="agent").public()}


@route("POST", r"/api/voicecall/agent/stt", permission=OWNER, auth="agent")
def agent_stt(ctx):
    s, c = _svc(), _call(ctx)
    return {"transcript": _speech(s).transcribe(s.store.get_call(c.call_id).owner_id, ctx.payload.get("audio_b64"),
                                                ctx.payload.get("mime_type"))}


@route("POST", r"/api/voicecall/agent/tts", permission=OWNER, auth="agent")
def agent_tts(ctx):
    import base64
    s = _svc()
    text = ctx.payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")
    audio, mime = _speech(s).speak_audio(text[:1500], "hood")
    return {"audio_b64": base64.b64encode(audio).decode("ascii"), "mime_type": mime}
