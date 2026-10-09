"""
HOOD Model Gateway - Mock Provider Adapter
Enables deterministic local testing, CI validation, and failure simulation.
"""

import time
from typing import Optional
from packages.contracts import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderName,
    ModelClass
)
from .base import BaseModelProvider, ProviderRateLimitError, ProviderError


class MockProviderAdapter(BaseModelProvider):
    def __init__(self, enabled: bool = True, simulate_failure: bool = False, failure_type: Optional[str] = None):
        super().__init__(ProviderName.MOCK, enabled=enabled)
        self.simulate_failure = simulate_failure
        self.failure_type = failure_type
        self.invocation_count = 0

    def is_healthy(self) -> bool:
        return self.enabled and not self.simulate_failure

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.invocation_count += 1

        if self.simulate_failure:
            if self.failure_type == "rate_limit":
                raise ProviderRateLimitError("Simulated mock rate limit exceeded")
            raise ProviderError("Simulated mock provider failure")

        model_name = {
            ModelClass.FAST: "mock-fast-v1",
            ModelClass.STANDARD: "mock-standard-v1",
            ModelClass.DEEP: "mock-deep-v1",
            ModelClass.SPECIALIST: "mock-specialist-v1"
        }.get(request.model_class, "mock-standard-v1")

        prompt_len = len(request.prompt.split())
        completion_text = f"Mock response from {model_name} for prompt: {request.prompt[:50]}..."
        comp_len = len(completion_text.split())

        # Cost calculation ($0.00 for mock)
        usage = ModelUsage(
            prompt_tokens=prompt_len * 2,
            completion_tokens=comp_len * 2,
            total_tokens=(prompt_len + comp_len) * 2,
            estimated_cost_usd=0.0
        )

        return ModelResponse(
            text=completion_text,
            provider=self.provider_name,
            model_name=model_name,
            usage=usage,
            latency_ms=10,
            is_mock=True,
            is_fallback=False
        )
