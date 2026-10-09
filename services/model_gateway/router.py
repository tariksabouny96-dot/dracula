"""
HOOD Model Router & Gateway Orchestrator
Governed by Master System Specification Section 8 & Build Instructions Section 9.
"""

from typing import Dict, List, Optional
from packages.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderName,
    ModelClass
)
from packages.config import SystemConfig, EnvironmentProfile
from .base import BaseModelProvider, ProviderError, ProviderNotConfiguredError, ProviderRateLimitError
from .mock_adapter import MockProviderAdapter
from .gemini_adapter import GeminiProviderAdapter
from .openai_adapter import OpenAIProviderAdapter
from .local_adapter import LocalProviderAdapter
from .cost_controller import CostController, BudgetExceededError
from packages.config.pricing import load_price_table, estimate_tokens, NON_BILLING_PROVIDERS, ModelPrice


class ModelRouter:
    """Provider-neutral model router with fallback support and cost enforcement."""

    def __init__(self, config: Optional[SystemConfig] = None, cost_controller: Optional[CostController] = None,
                 price_table: Optional[Dict[str, Dict[str, ModelPrice]]] = None):
        self.config = config or SystemConfig()
        self.cost_controller = cost_controller or CostController(self.config.budgets)
        self.price_table = price_table if price_table is not None else load_price_table()
        self.providers: Dict[ProviderName, BaseModelProvider] = {}
        self._init_providers()

    def _price_for(self, provider_name: ProviderName, provider: BaseModelProvider,
                   request: ModelRequest):
        """Return (model, price or None, billing). Unknown model on a paid provider => no price."""
        resolve = getattr(provider, "resolve_model", None)
        model = resolve(request) if callable(resolve) else None
        if provider_name.value in NON_BILLING_PROVIDERS:
            return model, None, False
        price = self.price_table.get(provider_name.value, {}).get(model) if model else None
        return model, price, True

    def _init_providers(self):
        # Register standard adapters
        gemini_cfg = self.config.providers.get("gemini")
        openai_cfg = self.config.providers.get("openai")
        mock_cfg = self.config.providers.get("mock")
        local_cfg = self.config.providers.get("local")

        self.providers[ProviderName.MOCK] = MockProviderAdapter(
            enabled=mock_cfg.enabled if mock_cfg else True
        )
        self.providers[ProviderName.GEMINI] = GeminiProviderAdapter(
            enabled=gemini_cfg.enabled if gemini_cfg else True
        )
        self.providers[ProviderName.OPENAI] = OpenAIProviderAdapter(
            enabled=openai_cfg.enabled if openai_cfg else False
        )
        self.providers[ProviderName.LOCAL] = LocalProviderAdapter(
            enabled=local_cfg.enabled if local_cfg else False
        )

    def estimate_max_cost(self, request: ModelRequest) -> float:
        """Worst-case USD cost of this request over the providers it may route to.

        Returns ``float('inf')`` when an eligible paid provider has no price, so
        callers enforcing a budget refuse rather than guess.
        """
        worst = 0.0
        for name in self.select_provider_order(request):
            provider = self.providers.get(name)
            if not provider or not provider.enabled:
                continue
            if request.allowed_providers and name not in request.allowed_providers:
                continue
            try:
                _, price, billing = self._price_for(name, provider, request)
            except ProviderError:
                continue
            if billing and price is None:
                return float("inf")
            if price is not None:
                prompt_tokens = estimate_tokens((request.system_prompt or "") + request.prompt)
                worst = max(worst, prompt_tokens / 1000.0 * price.input_per_1k_usd
                            + request.max_tokens / 1000.0 * price.output_per_1k_usd)
        return worst

    def register_provider(self, name: ProviderName, provider: BaseModelProvider):
        self.providers[name] = provider

    def _mock_authorized(self, request: ModelRequest) -> bool:
        """Never silently substitute fabricated content for an operational LLM."""
        return (
            self.config.environment == EnvironmentProfile.TEST
            or request.preferred_provider == ProviderName.MOCK
            or (request.allowed_providers is not None and ProviderName.MOCK in request.allowed_providers)
        )

    def select_provider_order(self, request: ModelRequest) -> List[ProviderName]:
        """Determine the provider order without implicit mock responses in normal use."""
        # If user explicitly preferred a provider and it's permitted:
        if request.preferred_provider and request.preferred_provider in self.providers:
            order = [request.preferred_provider] if request.preferred_provider != ProviderName.MOCK or self._mock_authorized(request) else []
            for p in [ProviderName.GEMINI, ProviderName.LOCAL, ProviderName.MOCK, ProviderName.OPENAI]:
                if p not in order and self.providers.get(p) and self.providers[p].enabled and (p != ProviderName.MOCK or self._mock_authorized(request)):
                    order.append(p)
            return order

        # Default routing order:
        # 1. Gemini (primary live online AI)
        # 2. Local (offline sovereign fallback if enabled)
        # 3. OpenAI (if configured/enabled)
        # 4. Mock (fallback for testing/isolated suites)
        order = []
        for p in [ProviderName.GEMINI, ProviderName.LOCAL, ProviderName.OPENAI, ProviderName.MOCK]:
            if self.providers.get(p) and self.providers[p].enabled and (p != ProviderName.MOCK or self._mock_authorized(request)):
                order.append(p)

        return order

    def invoke(self, request: ModelRequest) -> ModelResponse:
        is_deep = (request.model_class == ModelClass.DEEP)

        # 1. Budget pre-flight check
        if not self.cost_controller.can_execute(task_id=request.task_id, is_deep_model=is_deep):
            raise BudgetExceededError(
                f"Task {request.task_id} or system has exceeded configured budget limit."
            )

        candidate_order = self.select_provider_order(request)
        last_error = None
        attempt_count = 0

        for provider_name in candidate_order:
            provider = self.providers.get(provider_name)
            if not provider or not provider.enabled:
                continue

            # Respect allowed_providers if specified
            if request.allowed_providers and provider_name not in request.allowed_providers:
                continue

            try:
                model, price, billing = self._price_for(provider_name, provider, request)
            except ProviderError as e:
                last_error = e
                continue
            if billing and price is None:
                # Unknown cost: never send a paid request we cannot bound.
                last_error = ProviderNotConfiguredError(
                    f"No pricing metadata for {provider_name.value}/{model or 'unresolved model'}; "
                    "refusing paid call with unknown cost (configure HOOD_MODEL_PRICING)")
                continue
            estimate = 0.0
            if price is not None:
                prompt_tokens = estimate_tokens((request.system_prompt or "") + request.prompt)
                estimate = (prompt_tokens / 1000.0) * price.input_per_1k_usd + \
                           (request.max_tokens / 1000.0) * price.output_per_1k_usd
            reservation = self.cost_controller.reserve(request.task_id, estimate, is_deep_model=is_deep)

            try:
                attempt_count += 1
                resp = provider.invoke(request)
            except (ProviderNotConfiguredError, ProviderRateLimitError) as e:
                # Refused before generation: no spend is attributable.
                self.cost_controller.release(reservation)
                last_error = e
                continue
            except ProviderError as e:
                # Request may have been processed and billed: charge the reservation.
                from packages.contracts import ModelUsage
                self.cost_controller.settle(reservation, ModelUsage(), cost_measured=False,
                                            provider=provider_name.value, model=model or "unknown")
                last_error = e
                continue

            if resp.is_mock and not self._mock_authorized(request):
                self.cost_controller.release(reservation)
                last_error = ProviderError("Simulated output is not permitted for this request")
                continue

            # If this was not the first candidate or differs from preferred provider, mark as fallback
            if attempt_count > 1 or (request.preferred_provider and provider_name != request.preferred_provider) or candidate_order.index(provider_name) > 0:
                resp.is_fallback = True

            measured = True
            if price is not None:
                if resp.usage.total_tokens > 0:
                    resp.usage.estimated_cost_usd = (
                        resp.usage.prompt_tokens / 1000.0 * price.input_per_1k_usd +
                        resp.usage.completion_tokens / 1000.0 * price.output_per_1k_usd)
                else:
                    measured = False  # provider omitted usage: charge the full reservation
            self.cost_controller.settle(
                reservation, resp.usage, cost_measured=measured, provider=resp.provider.value,
                model=resp.model_name, latency_ms=resp.latency_ms, is_fallback=resp.is_fallback,
                price_source=(f"{price.source} ({price.as_of})" if price else "non-billing provider"))
            return resp

        # If all providers failed
        if last_error:
            raise ProviderError(f"All model providers failed. Last error: {str(last_error)}")
        raise ProviderError("No eligible model providers available for request.")
