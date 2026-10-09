"""
HOOD Model Gateway - Base Provider Interface
Governed by Master System Specification Section 8 & Build Instructions Section 9.
"""

from abc import ABC, abstractmethod
from packages.contracts import ModelRequest, ModelResponse, ProviderName


class ProviderError(Exception):
    """Base error for model providers."""
    pass


class ProviderNotConfiguredError(ProviderError):
    """Raised when an unauthorized/unconfigured provider is called."""
    pass


class ProviderRateLimitError(ProviderError):
    """Raised when a provider hits rate limits or quotas."""
    pass


class BaseModelProvider(ABC):
    """Vendor-neutral model provider interface."""

    def __init__(self, provider_name: ProviderName, enabled: bool = True):
        self.provider_name = provider_name
        self.enabled = enabled

    @abstractmethod
    def is_healthy(self) -> bool:
        """Checks if provider is ready and responsive."""
        pass

    @abstractmethod
    def invoke(self, request: ModelRequest) -> ModelResponse:
        """Executes a model generation request."""
        pass
