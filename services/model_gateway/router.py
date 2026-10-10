"""
HOOD Model Router & Gateway Orchestrator
Governed by Master System Specification Section 8 & Build Instructions Section 9.
"""

import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
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
from .gemini_usage import price_note
from packages.config.pricing import load_price_table, estimate_tokens, NON_BILLING_PROVIDERS, ModelPrice


class ModelRouter:
    """Provider-neutral model router with fallback support and cost enforcement."""

    # Known outbound host per provider; consulted by the egress firewall before
    # a live call. MOCK makes no outbound connection, so it has no entry.
    _PROVIDER_EGRESS_HOST = {
        ProviderName.GEMINI: "generativelanguage.googleapis.com",
        ProviderName.OPENAI: "api.openai.com",
    }

    def __init__(self, config: Optional[SystemConfig] = None, cost_controller: Optional[CostController] = None,
                 price_table: Optional[Dict[str, Dict[str, ModelPrice]]] = None, firewall: Optional[object] = None,
                 vault: Optional[object] = None):
        self.config = config or SystemConfig()
        # The runtime's vault: the key saved in Settings and the one the adapter reads are the same.
        self.vault = vault
        self.cost_controller = cost_controller or CostController(self.config.budgets)
        self.price_table = price_table if price_table is not None else load_price_table()
        # Optional egress firewall. When set, a live provider call is refused
        # unless the provider's host is allowed (default deny). None keeps the
        # legacy behaviour for callers that have not wired a firewall yet.
        self.firewall = firewall
        self.providers: Dict[ProviderName, BaseModelProvider] = {}
        # What this process actually observed per provider (chat, missions, tests
        # of the connection): the source of truth for "is the AI working now".
        self._observed: Dict[str, Dict[str, Any]] = {}
        self._observed_lock = threading.Lock()
        self._init_providers()

    def _price_for(self, provider_name: ProviderName, provider: BaseModelProvider,
                   request: ModelRequest):
        """Return (model, price or None, billing). Unknown model on a paid provider => no price."""
        resolve = getattr(provider, "resolve_model", None)
        model = resolve(request) if callable(resolve) else None
        if provider_name.value in NON_BILLING_PROVIDERS:
            return model, None, False
        table = self.price_table.get(provider_name.value, {})
        # Every model the adapter may fall back to must be priced; budget for the dearest one.
        candidates = getattr(provider, "candidate_models", None)
        names = candidates(request) if callable(candidates) else [model]
        if not names or any(n is None or n not in table for n in names):
            return model, None, True
        # Reserve with the candidate that would cost the most for THIS request (a fallback with a
        # higher output price can cost more than a "dearer" primary on a short prompt).
        prompt_tokens = estimate_tokens((request.system_prompt or "") + request.prompt)
        price = max((table[n] for n in names),
                    key=lambda p: prompt_tokens * p.input_per_1k_usd + request.max_tokens * p.output_per_1k_usd)
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
            vault=self.vault,
            enabled=gemini_cfg.enabled if gemini_cfg else True,
            api_key_secret_ref=(gemini_cfg.api_key_secret_ref if gemini_cfg else None) or "SECRET://gemini/api_key",
            timeout_sec=max(60, int(gemini_cfg.timeout_sec)) if gemini_cfg else 180,
            models=({ModelClass.FAST: gemini_cfg.default_fast_model,
                     ModelClass.STANDARD: gemini_cfg.default_standard_model,
                     ModelClass.DEEP: gemini_cfg.default_deep_model} if gemini_cfg else None),
        )
        self.providers[ProviderName.OPENAI] = OpenAIProviderAdapter(
            enabled=openai_cfg.enabled if openai_cfg else False
        )
        self.providers[ProviderName.LOCAL] = LocalProviderAdapter(
            enabled=local_cfg.enabled if local_cfg else False
        )

    def _observe(self, provider_name: ProviderName, ok: bool, model: Optional[str] = None,
                 error: Optional[BaseException] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._observed_lock:
            rec = self._observed.setdefault(provider_name.value, {
                "last_success_at": None, "last_model": None, "last_failure_at": None, "last_error": None})
            if ok:
                rec["last_success_at"], rec["last_model"] = now, model
            else:
                rec["last_failure_at"] = now
                rec["last_error"] = f"{type(error).__name__}: {error}"[:300] if error else "failed"

    def observed_health(self, provider_name: ProviderName) -> Dict[str, Any]:
        """Last success/failure this process saw for a provider (empty if never called)."""
        with self._observed_lock:
            return dict(self._observed.get(provider_name.value, {}))

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
        possibly_sent = False  # True once any request may have reached a provider

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
            # Egress firewall: a live provider call must be allowed by policy
            # (default deny). Refused before any spend, so no reservation is made.
            egress_host = self._PROVIDER_EGRESS_HOST.get(provider_name)
            if self.firewall is not None and egress_host is not None:
                decision = self.firewall.authorize(egress_host, 443, purpose=f"model:{provider_name.value}")
                if not decision.allowed:
                    last_error = ProviderNotConfiguredError(
                        f"Egress to {egress_host} for {provider_name.value} is blocked by the "
                        f"HOOD firewall: {decision.reason}. Allow it as the Root Owner to enable this provider.")
                    self._observe(provider_name, False, error=last_error)
                    continue

            reservation = self.cost_controller.reserve(request.task_id, estimate, is_deep_model=is_deep)

            try:
                attempt_count += 1
                resp = provider.invoke(request)
            except (ProviderNotConfiguredError, ProviderRateLimitError) as e:
                if getattr(e, "possibly_billed", False):
                    # An earlier attempt may have been processed (timeout): charge the reservation.
                    from packages.contracts import ModelUsage
                    self.cost_controller.settle(reservation, ModelUsage(), cost_measured=False,
                                                provider=provider_name.value, model=model or "unknown")
                    possibly_sent = True
                else:
                    # Refused before generation: no spend is attributable.
                    self.cost_controller.release(reservation)
                self._observe(provider_name, False, error=e)
                last_error = e
                continue
            except ProviderError as e:
                possibly_sent = True
                # Request may have been processed and billed: charge the reservation.
                from packages.contracts import ModelUsage
                self.cost_controller.settle(reservation, ModelUsage(), cost_measured=False,
                                            provider=provider_name.value, model=model or "unknown")
                self._observe(provider_name, False, error=e)
                last_error = e
                continue

            possibly_sent = True
            if resp.is_mock and not self._mock_authorized(request):
                self.cost_controller.release(reservation)
                last_error = ProviderError("Simulated output is not permitted for this request")
                continue

            # If this was not the first candidate or differs from preferred provider, mark as fallback
            if attempt_count > 1 or (request.preferred_provider and provider_name != request.preferred_provider) or candidate_order.index(provider_name) > 0:
                resp.is_fallback = True

            measured = True
            billed = price
            if price is not None:
                # Bill the model that actually answered (a fallback is often cheaper); the dearest
                # candidate's price was only for the pre-flight reservation.
                table = self.price_table.get(provider_name.value, {})
                billed = table.get(resp.requested_model or "") or table.get(resp.model_name or "") or price
                if resp.usage.total_tokens > 0:
                    resp.usage.estimated_cost_usd = billed.cost(resp.usage.prompt_tokens, resp.usage.completion_tokens,
                                                                getattr(resp, "audio_prompt_tokens", 0))
                    if getattr(resp, "uncertain_attempts", 0):
                        # Earlier attempts may have been billed too: never record less than reserved.
                        resp.usage.estimated_cost_usd = max(resp.usage.estimated_cost_usd, estimate)
                else:
                    measured = False  # provider omitted usage: charge the full reservation
            self.cost_controller.settle(
                reservation, resp.usage, cost_measured=measured, provider=resp.provider.value,
                model=resp.requested_model or resp.model_name, latency_ms=resp.latency_ms,
                is_fallback=resp.is_fallback,
                price_source=(price_note(billed, getattr(resp, "audio_prompt_tokens", 0)) if billed
                              else "non-billing provider"))
            if not resp.is_mock:
                self._observe(provider_name, True, model=resp.model_name)
            return resp

        # If all providers failed. When nothing could have been sent, say so precisely so
        # callers do not charge budgets for a request that never left the machine.
        if last_error:
            cls = ProviderError if possibly_sent else ProviderNotConfiguredError
            raise cls(f"All model providers failed. Last error: {str(last_error)}")
        raise ProviderNotConfiguredError("No eligible model providers available for request.")
