"""The provider badge reflects what is true now (owner's Windows run: it said
DEGRADED because of failures from before the key was set, and chat successes
never counted)."""
import pytest

from packages.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderName
from services.console import api as console
from services.model_gateway.base import BaseModelProvider, ProviderRateLimitError
from services.model_gateway.router import ModelRouter


class FlakyLocal(BaseModelProvider):
    """Non-billing provider that fails once, then succeeds."""

    def __init__(self):
        super().__init__(ProviderName.LOCAL)
        self.fail_next = True

    def is_healthy(self):
        return True

    def invoke(self, request):
        if self.fail_next:
            self.fail_next = False
            raise ProviderRateLimitError("busy")
        return ModelResponse(text="ok", provider=ProviderName.LOCAL, model_name="local-model",
                             usage=ModelUsage(), latency_ms=1, is_mock=False, is_fallback=False)


def _request():
    return ModelRequest(prompt="hi", preferred_provider=ProviderName.LOCAL,
                        allowed_providers=[ProviderName.LOCAL])


def _state(name):
    return next(p for p in console._provider_health(None) if p["id"] == name)


@pytest.fixture
def router(monkeypatch):
    r = ModelRouter(price_table={})
    r.register_provider(ProviderName.LOCAL, FlakyLocal())
    monkeypatch.setitem(console.SERVICES, "router", r)
    return r


def test_router_records_failure_then_success(router):
    with pytest.raises(Exception):
        router.invoke(_request())
    seen = router.observed_health(ProviderName.LOCAL)
    assert seen["last_failure_at"] and "busy" in seen["last_error"]
    assert _state("local")["state"] == "degraded"
    assert "busy" in _state("local")["last_error"]

    router.invoke(_request())                       # any caller (chat, mission, test) counts
    assert _state("local")["state"] == "verified_online"


def test_configured_but_unused_is_ready_not_degraded(router):
    assert _state("local")["state"] == "configured"


def test_key_without_pricing_is_needs_pricing(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    r = ModelRouter(price_table={})                 # key present, no prices
    monkeypatch.setitem(console.SERVICES, "router", r)
    assert _state("gemini")["state"] == "needs_pricing"


def test_no_key_is_not_configured(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HOOD_GEMINI_CREDENTIAL"):
        monkeypatch.delenv(k, raising=False)
    r = ModelRouter(price_table={})
    monkeypatch.setitem(console.SERVICES, "router", r)
    assert _state("gemini")["state"] == "not_configured"
