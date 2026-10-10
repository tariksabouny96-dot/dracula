"""Settings > Model provider: the owner manages the Gemini key and prices in the UI.

- The key is stored in HOOD's encrypted vault (the same one the Gemini adapter
  reads first), takes effect immediately, and is never sent back: only whether
  one is set, where it comes from, and its last 4 characters.
- Prices are saved to <HOOD_DATA_DIR>/model_pricing.json, which takes precedence
  over HOOD_MODEL_PRICING, and are applied to the running router at once.
- Owner only (OWNERSHIP_ADMIN); changes need explicit confirmation; the server
  enforces Host, CSRF and session before these run.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date

from packages.config.pricing import load_price_table, settings_pricing_file
from packages.contracts import ModelClass, ModelRequest, ProviderName
from ui.routes import SERVICES, ServiceUnavailable, route

KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{20,200}$")
SECRET_PROVIDER, SECRET_NAME = "gemini", "api_key"


def _router():
    r = SERVICES.get("router")
    if r is None:
        raise ServiceUnavailable("Model router is not attached")
    return r


def _gemini():
    g = _router().providers.get(ProviderName.GEMINI)
    if g is None:
        raise ServiceUnavailable("Gemini adapter is not attached")
    return g


def _models_in_use():
    """Every Gemini model HOOD may call: tiers, fallbacks and voice."""
    g = _gemini()
    names = []
    for cls in (ModelClass.STANDARD, ModelClass.FAST, ModelClass.DEEP):
        for n in g.candidate_models(ModelRequest(prompt="", model_class=cls)):
            if n not in names:
                names.append(n)
    from services.voice.cloud import stt_model, tts_model
    for n in (stt_model(), tts_model()):
        if n not in names:
            names.append(n)
    return names


def _key_info():
    g = _gemini()
    stored = g.vault.get_secret(g.api_key_secret_ref)
    if stored:
        return {"set": True, "source": "settings", "hint": "…" + stored[-4:]}
    env = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env:
        return {"set": True, "source": "environment (.env)", "hint": "…" + env[-4:]}
    if g._proxy_credential():
        return {"set": True, "source": "credential proxy", "hint": None}
    return {"set": False, "source": None, "hint": None}


def _pricing_info():
    table = (_router().price_table or {}).get("gemini", {})
    if settings_pricing_file().is_file():
        source = "Settings"
    elif os.environ.get("HOOD_MODEL_PRICING"):
        source = "HOOD_MODEL_PRICING (" + os.environ["HOOD_MODEL_PRICING"] + ")"
    else:
        source = None
    models = {}
    for name in _models_in_use():
        p = table.get(name)
        models[name] = None if p is None else {"input_per_1k_usd": p.input_per_1k_usd,
                                               "output_per_1k_usd": p.output_per_1k_usd,
                                               "as_of": p.as_of, "source": p.source}
    return {"source": source, "models": models, "missing": [m for m, v in models.items() if v is None]}


@route("GET", r"/api/settings/model", permission="OWNERSHIP_ADMIN")
def get_model_settings(ctx):
    from services.console.api import _provider_health
    gem = next((p for p in _provider_health(SERVICES.get("agents")) if p["id"] == "gemini"), {})
    return {"provider": "gemini", "key": _key_info(), "pricing": _pricing_info(),
            "state": gem.get("state"), "last_success_at": gem.get("last_success_at"),
            "last_error": gem.get("last_error")}


@route("POST", r"/api/settings/model/key", permission="OWNERSHIP_ADMIN")
def set_key(ctx):
    ctx.require_confirm("store this Gemini API key in HOOD's encrypted vault")
    key = ctx.payload.get("api_key")
    if not isinstance(key, str) or not KEY_RE.match(key.strip()):
        raise ValueError("That doesn't look like an API key (20-200 letters, digits, - _ .)")
    g = _gemini()
    g.vault.set_secret(SECRET_PROVIDER, SECRET_NAME, key.strip(), description="Set in Settings > Model provider")
    return {"key": _key_info()}


@route("POST", r"/api/settings/model/key/remove", permission="OWNERSHIP_ADMIN")
def remove_key(ctx):
    ctx.require_confirm("remove the Gemini API key saved in Settings")
    g = _gemini()
    g.vault.delete_secret(g.api_key_secret_ref)
    return {"key": _key_info()}


def _price(value, field):
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a number (USD per 1,000 tokens)")
    if not 0 <= v <= 10:
        raise ValueError(f"{field} must be between 0 and 10 USD per 1,000 tokens")
    return v


@route("POST", r"/api/settings/model/pricing", permission="OWNERSHIP_ADMIN")
def set_pricing(ctx):
    mode = ctx.payload.get("mode")
    today = date.today().isoformat()
    models = _models_in_use()
    if mode == "free":
        ctx.require_confirm("declare $0 prices: only correct if your Google project has NO billing account")
        entries = {m: {"input_per_1k_usd": 0.0, "output_per_1k_usd": 0.0, "as_of": today,
                       "source": "owner-declared free tier in Settings (no billing account)"} for m in models}
    elif mode == "custom":
        ctx.require_confirm("save these model prices")
        given = ctx.payload.get("prices")
        if not isinstance(given, dict):
            raise ValueError("prices must be an object: {model: {input_per_1k_usd, output_per_1k_usd}}")
        entries = {}
        for m in models:
            p = given.get(m)
            if not isinstance(p, dict):
                raise ValueError(f"missing prices for {m}")
            entries[m] = {"input_per_1k_usd": _price(p.get("input_per_1k_usd"), f"{m} input"),
                          "output_per_1k_usd": _price(p.get("output_per_1k_usd"), f"{m} output"),
                          "as_of": today, "source": "owner-entered in Settings"}
    else:
        raise ValueError("mode must be 'free' or 'custom'")
    path = settings_pricing_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"_comment": "Saved from HOOD Settings > Model provider.", "gemini": entries},
                              indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _router().price_table = load_price_table(str(path))   # takes effect now, no restart
    return {"pricing": _pricing_info()}


@route("POST", r"/api/settings/model/test", permission="OWNERSHIP_ADMIN")
def test_connection(ctx):
    """One tiny real call through the normal governed path (firewall, price, budget)."""
    from services.model_gateway.base import ProviderError
    from services.model_gateway.cost_controller import BudgetExceededError
    req = ModelRequest(prompt="Reply with exactly: OK", preferred_provider=ProviderName.GEMINI,
                       allowed_providers=[ProviderName.GEMINI], model_class=ModelClass.FAST,
                       max_tokens=512, temperature=0)
    try:
        resp = _router().invoke(req)
    except (ProviderError, BudgetExceededError) as exc:
        return {"ok": False, "error": str(exc)[:400]}
    return {"ok": True, "model": resp.model_name, "latency_ms": resp.latency_ms, "reply": resp.text[:80]}


# ---------------------------------------------------------------- Settings > Voice (ElevenLabs)
def _voice_view():
    from services.voice import elevenlabs as eleven
    cfg = eleven.load_settings()
    stored = _gemini().vault.get_secret(eleven.SECRET_REF)
    return {**cfg, "key": {"set": bool(stored), "hint": ("…" + stored[-4:]) if stored else None},
            "default_model": eleven.DEFAULT_MODEL}


@route("GET", r"/api/settings/voice", permission="OWNERSHIP_ADMIN")
def get_voice_settings(ctx):
    return _voice_view()


@route("POST", r"/api/settings/voice/key", permission="OWNERSHIP_ADMIN")
def set_voice_key(ctx):
    from services.voice import elevenlabs as eleven
    ctx.require_confirm("store this ElevenLabs API key in HOOD's encrypted vault and allow api.elevenlabs.io")
    key = ctx.payload.get("api_key")
    if not isinstance(key, str) or not KEY_RE.match(key.strip()):
        raise ValueError("That doesn't look like an API key (20-200 letters, digits, - _ .)")
    _gemini().vault.set_secret(eleven.SECRET_PROVIDER, eleven.SECRET_NAME, key.strip(),
                               description="ElevenLabs voice, set in Settings > Voice")
    fw = SERVICES.get("firewall")
    if fw is not None and not any(r.get("host") == eleven.HOST for r in fw.list_rules()):
        # The owner saving the key is the owner allowing this destination.
        fw.allow(eleven.HOST, [443], note="ElevenLabs voice (owner, Settings > Voice)",
                 added_by=ctx.username, is_root_owner=True)
    return _voice_view()


@route("POST", r"/api/settings/voice/key/remove", permission="OWNERSHIP_ADMIN")
def remove_voice_key(ctx):
    from services.voice import elevenlabs as eleven
    ctx.require_confirm("remove the ElevenLabs API key")
    _gemini().vault.delete_secret(eleven.SECRET_REF)
    if eleven.load_settings()["tts_provider"] == "elevenlabs":
        eleven.save_settings({"tts_provider": "gemini"})     # fall back to Gemini's voice
    return _voice_view()


@route("POST", r"/api/settings/voice", permission="OWNERSHIP_ADMIN")
def set_voice_settings(ctx):
    from services.voice import elevenlabs as eleven
    ctx.require_confirm("save the voice settings")
    allowed = ("tts_provider", "voice_id", "x_voice_id", "model_id", "price_per_1k_chars")
    eleven.save_settings({k: ctx.payload[k] for k in allowed if k in ctx.payload})
    return _voice_view()
