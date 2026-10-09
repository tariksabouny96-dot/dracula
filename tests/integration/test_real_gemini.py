"""
HOOD v0.1 Integration Tests - Real Gemini Connectivity & Cost Ledger
Verifies live authenticated connection, token/latency capture, zero-cost tracking for free-tier Gemini,
and fallback logic.
"""

import pytest
from pathlib import Path
from packages.config import load_config
from packages.contracts import ModelRequest, ModelClass, ProviderName
from packages.auth.vault import SecretVault
from services.model_gateway.router import ModelRouter
from services.model_gateway.cost_controller import CostController
from services.model_gateway.gemini_adapter import GeminiProviderAdapter


@pytest.mark.live_provider
def test_gemini_adapter_live_call():
    """Verifies that the GeminiProviderAdapter successfully connects to Google Gemini API using vault credentials."""
    config = load_config()
    vault = SecretVault(Path(config.security.secret_vault_file))
    adapter = GeminiProviderAdapter(vault=vault)
    assert adapter.is_healthy() is True

    req = ModelRequest(
        model_class=ModelClass.FAST,
        prompt="Respond with exact word 'PONG'.",
        temperature=0.0
    )
    resp = adapter.invoke(req)

    assert resp.provider == ProviderName.GEMINI
    assert resp.model_name in ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-flash-latest")
    assert resp.text is not None and len(resp.text.strip()) > 0
    assert resp.latency_ms > 0
    assert resp.usage.total_tokens > 0
    assert resp.usage.estimated_cost_usd == 0.0  # Free tier verification


@pytest.mark.live_provider
def test_model_router_with_cost_ledger():
    """Verifies that ModelRouter records live calls into the CostController call ledger."""
    config = load_config()
    cost_controller = CostController(config.budgets)
    router = ModelRouter(config, cost_controller)

    initial_calls = len(cost_controller.call_history)
    req = ModelRequest(
        model_class=ModelClass.FAST,
        prompt="Hello HOOD",
        task_id="integration-test-01"
    )
    resp = router.invoke(req)

    assert resp.text is not None
    assert len(cost_controller.call_history) == initial_calls + 1
    last_call = cost_controller.call_history[-1]
    assert last_call["task_id"] == "integration-test-01"
    assert last_call["provider"] == "gemini"
    assert last_call["is_free_tier"] is True
    assert last_call["cost_usd"] == 0.0
