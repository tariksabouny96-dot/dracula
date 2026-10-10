"""Live status for capability cards, from what HOOD can observe right now.

Each probe reads the shared service instances (ui/routes.SERVICES) at request
time. Probes are cheap and never make a model call: state comes from
configuration and from what the running services have actually seen. When a
service is not attached, the probe says so instead of guessing.
"""
from __future__ import annotations

import functools

from services.capabilities.registry import register_state_provider
from ui.routes import SERVICES


def _provider_state(name: str):
    from services.console.api import _provider_health
    return next((p for p in _provider_health(SERVICES.get("agents")) if p["id"] == name), None)


def _model_card(name: str, label: str):
    def probe():
        p = _provider_state(name)
        if p is None or p["configured"] is None:
            return {"state": "unknown", "detail": "Model router not attached"}
        if p["state"] == "not_configured":
            if name == "openai":
                return {"state": "not_configured", "detail": "Off: needs the owner's explicit opt-in"}
            return {"state": "not_configured", "detail": f"No {label} API key — set it in Settings"}
        if p["state"] == "needs_pricing":
            return {"state": "not_configured", "detail": "Key set, but no price on file: calls are refused (Settings)"}
        if p["state"] == "degraded":
            return {"state": "degraded", "detail": "Last call failed: " + str(p.get("last_error") or "")[:160]}
        if p["state"] == "verified_online":
            return {"state": "verified_online", "detail": f"Last successful call {p['last_success_at'][:19]}Z"}
        return {"state": "available", "detail": "Configured; not used yet since HOOD started"}
    return probe


@functools.lru_cache(maxsize=1)
def _sandbox_available() -> bool:
    from services.agents.sandbox import network_isolation_available
    return network_isolation_available()


def _orchestration():
    if SERVICES.get("agents") is None:
        return {"state": "unavailable", "detail": "Agent engine not attached"}
    if not _sandbox_available():
        return {"state": "degraded", "detail": "Website missions work here (checked without running code). "
                                               "Python missions need the Linux sandbox (WSL2) or your "
                                               "\"Run on my PC\" approval for each mission"}
    return {"state": "available", "detail": "Planner, engineer, QA and verifier ready; sandbox available"}


def _approvals():
    svc = SERVICES.get("approvals")
    if svc is None:
        return {"state": "unavailable", "detail": "Approval service not attached"}
    n = len(svc.list_pending())
    return {"state": "awaiting_approval" if n else "available",
            "detail": f"{n} approval(s) waiting for you" if n else "No approvals waiting"}


def _x_executive():
    svc = SERVICES.get("x")
    if svc is None:
        return {"state": "unavailable", "detail": "X session manager not attached"}
    return {"state": "available", "detail": svc.get_status().get("badge") or "X: DORMANT"}


def _emergency():
    stop = SERVICES.get("emergency_stop")
    if stop is None:
        return {"state": "unavailable", "detail": "Emergency stop not attached"}
    if stop.is_active:
        return {"state": "blocked", "detail": "ENGAGED — execution halted: " + (stop.stop_reason or "")[:120]}
    return {"state": "available", "detail": "Armed, not engaged"}


def _firewall():
    fw = SERVICES.get("firewall")
    if fw is None:
        return {"state": "unavailable", "detail": "Firewall not attached"}
    rules = fw.list_rules()
    return {"state": "available", "detail": f"Default deny; {len(rules)} owner-allowed destination(s)"}


def _learning():
    svc = SERVICES.get("learning")
    if svc is None:
        return {"state": "unavailable", "detail": "Learning service not attached"}
    mode = "semantic" if getattr(svc, "embedder", None) is not None else "lexical"
    return {"state": "available", "detail": f"Recording lessons from missions; {mode} recall"}


def _self_development():
    svc = SERVICES.get("selfdev")
    if svc is None:
        return {"state": "unavailable", "detail": "Self-development controller not attached"}
    waiting = sum(1 for p in svc.list() if p.get("state") == "AWAITING_APPROVAL")
    return {"state": "awaiting_approval" if waiting else "available",
            "detail": f"{waiting} change proposal(s) waiting for your approval" if waiting
            else "No change proposals waiting (nothing is applied without your approval)"}


def _memory():
    if SERVICES.get("memory") is None:
        return {"state": "unavailable", "detail": "Memory service not attached"}
    return {"state": "available", "detail": "Per-user memory store attached"}


def _browser():
    from services.browser.browser_service import PLAYWRIGHT_AVAILABLE
    if not PLAYWRIGHT_AVAILABLE:
        return {"state": "not_configured", "detail": "Playwright not installed (pip install -r requirements.txt)"}
    return {"state": "available", "detail": "Playwright installed; each browsing action still needs approval"}


for _cid, _fn in (("conversation", _model_card("gemini", "Gemini")),
                  ("gemini", _model_card("gemini", "Gemini")),
                  ("openai", _model_card("openai", "OpenAI")),
                  ("orchestrator", _orchestration), ("governance", _approvals), ("x", _x_executive),
                  ("emergency", _emergency), ("firewall", _firewall), ("learning", _learning),
                  ("self_development", _self_development), ("memory", _memory), ("browser", _browser)):
    register_state_provider(_cid, _fn)
