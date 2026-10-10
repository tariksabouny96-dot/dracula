"""Capability cards show observed live state instead of 'unknown' everywhere."""
from services.capabilities import live_probes  # noqa: F401  (registers probes)
from services.capabilities.registry import get_capability_inventory, live_state
from services.model_gateway.router import ModelRouter
from ui.routes import SERVICES


class Stop:
    def __init__(self, active):
        self.is_active, self.stop_reason = active, "owner pressed stop"


def test_core_cards_have_live_probes(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HOOD_GEMINI_CREDENTIAL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setitem(SERVICES, "router", ModelRouter(price_table={}))
    monkeypatch.setitem(SERVICES, "emergency_stop", Stop(False))
    assert live_state("conversation") == {"state": "not_configured",
                                          "detail": "No Gemini API key — set it in Settings"}
    assert live_state("emergency")["detail"] == "Armed, not engaged"
    monkeypatch.setitem(SERVICES, "emergency_stop", Stop(True))
    assert live_state("emergency")["state"] == "blocked"
    items = {i["id"]: i["live"]["state"] for i in get_capability_inventory()["items"]}
    for cid in ("conversation", "gemini", "orchestrator", "governance", "x", "emergency",
                "firewall", "learning", "self_development", "memory", "browser"):
        assert cid in items


def test_missing_service_is_reported_not_guessed(monkeypatch):
    monkeypatch.delitem(SERVICES, "firewall", raising=False)
    assert live_state("firewall") == {"state": "unavailable", "detail": "Firewall not attached"}
