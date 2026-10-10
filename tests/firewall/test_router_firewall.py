"""The model gateway must refuse a live provider the firewall has not allowed."""
import pytest

from packages.config import SystemConfig
from packages.config.pricing import ModelPrice
from packages.contracts import ModelRequest, ModelClass, ModelResponse, ModelUsage, ProviderName
from services.model_gateway.base import BaseModelProvider
from services.model_gateway.cost_controller import CostController
from services.model_gateway.router import ModelRouter
from services.firewall.policy import NetworkFirewall


class _SentinelGemini(BaseModelProvider):
    """A stand-in GEMINI provider that records whether it was actually called."""

    def __init__(self):
        super().__init__(ProviderName.GEMINI, enabled=True)
        self.called = False

    def is_healthy(self):
        return True

    def resolve_model(self, request):
        return "gemini-test"

    def candidate_models(self, request):
        return ["gemini-test"]

    def invoke(self, request):
        self.called = True
        return ModelResponse(text="ok", provider=ProviderName.GEMINI, model_name="gemini-test",
                             usage=ModelUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2,
                                              estimated_cost_usd=0.0), latency_ms=1)


def _router(firewall):
    cfg = SystemConfig()
    price_table = {"gemini": {"gemini-test": ModelPrice(input_per_1k_usd=0.0, output_per_1k_usd=0.0, as_of="2026-10-10", source="test")}}
    router = ModelRouter(cfg, CostController(cfg.budgets), price_table=price_table, firewall=firewall)
    sentinel = _SentinelGemini()
    router.providers[ProviderName.GEMINI] = sentinel
    # Remove other live providers so GEMINI is the only candidate; keep MOCK off.
    router.providers[ProviderName.OPENAI].enabled = False
    router.providers[ProviderName.LOCAL].enabled = False
    router.providers[ProviderName.MOCK].enabled = False
    return router, sentinel


def test_firewall_blocks_unallowed_provider(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")  # default deny
    router, sentinel = _router(fw)
    req = ModelRequest(model_class=ModelClass.FAST, prompt="hi", task_id="t1",
                       preferred_provider=ProviderName.GEMINI)
    with pytest.raises(Exception):
        router.invoke(req)
    assert sentinel.called is False, "provider was called despite the firewall denying its host"


def test_firewall_allows_provider_once_owner_permits(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    fw.allow("generativelanguage.googleapis.com", [443], is_root_owner=True)
    router, sentinel = _router(fw)
    req = ModelRequest(model_class=ModelClass.FAST, prompt="hi", task_id="t2",
                       preferred_provider=ProviderName.GEMINI)
    resp = router.invoke(req)
    assert sentinel.called is True and resp.text == "ok"
