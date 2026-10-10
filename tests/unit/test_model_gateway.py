import pytest
from packages.contracts import (
    ModelRequest,
    ModelClass,
    ProviderName
)
from packages.config import SystemConfig, BudgetSettings, ModelProviderConfig
from services.model_gateway.router import ModelRouter
from services.model_gateway.mock_adapter import MockProviderAdapter
from services.model_gateway.openai_adapter import OpenAIProviderAdapter
from services.model_gateway.base import ProviderNotConfiguredError
from services.model_gateway.cost_controller import CostController, BudgetExceededError

def test_openai_disabled_by_default():
    adapter = OpenAIProviderAdapter(enabled=False)
    assert adapter.is_healthy() is False

    req = ModelRequest(prompt="Hello")
    with pytest.raises(ProviderNotConfiguredError) as exc:
        adapter.invoke(req)
    assert "OpenAI provider is currently DISABLED" in str(exc.value)

def test_model_router_fallback():
    # Setup router where primary fails, fallback to mock
    cfg = SystemConfig()
    router = ModelRouter(cfg)

    # Register a failing primary adapter
    failing_adapter = MockProviderAdapter(enabled=True, simulate_failure=True)
    router.register_provider(ProviderName.GEMINI, failing_adapter)

    # Register working mock as secondary
    working_mock = MockProviderAdapter(enabled=True, simulate_failure=False)
    router.register_provider(ProviderName.MOCK, working_mock)

    req = ModelRequest(
        model_class=ModelClass.STANDARD,
        prompt="Test prompt",
        task_id="task-01",
        allowed_providers=[ProviderName.GEMINI, ProviderName.MOCK]
    )

    resp = router.invoke(req)
    # Must succeed via fallback
    assert resp.provider == ProviderName.MOCK
    assert resp.is_fallback is True

def test_cost_controller_budget_limit():
    budgets = BudgetSettings(max_task_spend_usd=0.05, max_task_model_calls=2)
    controller = CostController(budgets)

    assert controller.can_execute("task-01") is True
    # Record first call
    from packages.contracts import ModelUsage
    controller.record_usage("task-01", ModelUsage(estimated_cost_usd=0.03, total_tokens=100))

    # Second call should still pass
    assert controller.can_execute("task-01") is True
    controller.record_usage("task-01", ModelUsage(estimated_cost_usd=0.03, total_tokens=100))

    # Third call should exceed max calls and max task spend
    assert controller.can_execute("task-01") is False
