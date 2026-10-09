"""Operational model responses must not be replaced by fabricated mock answers."""
import pytest
from packages.config import EnvironmentProfile, SystemConfig, ModelProviderConfig
from packages.contracts import ModelRequest, ProviderName
from services.model_gateway.base import ProviderError
from services.model_gateway.router import ModelRouter
from services.model_gateway.mock_adapter import MockProviderAdapter


def failing_router(environment=EnvironmentProfile.DEVELOPMENT):
    cfg = SystemConfig(environment=environment)
    cfg.providers['gemini'] = ModelProviderConfig(enabled=True)
    cfg.providers['mock'] = ModelProviderConfig(enabled=True)
    router = ModelRouter(cfg)
    router.register_provider(ProviderName.GEMINI, MockProviderAdapter(enabled=True, simulate_failure=True))
    return router


def test_operational_request_does_not_silently_fallback_to_mock():
    router = failing_router()
    with pytest.raises(ProviderError, match='All model providers failed'):
        router.invoke(ModelRequest(prompt='Give real evidence'))
    assert router.providers[ProviderName.MOCK].invocation_count == 0


def test_explicit_mock_allowed_is_labeled():
    response = failing_router().invoke(ModelRequest(prompt='test', allowed_providers=[ProviderName.GEMINI, ProviderName.MOCK]))
    assert response.is_mock is True
    assert response.provider == ProviderName.MOCK
    assert response.is_fallback is True


def test_explicit_preferred_mock_is_labeled():
    response = failing_router().invoke(ModelRequest(prompt='test', preferred_provider=ProviderName.MOCK))
    assert response.is_mock is True


def test_test_profile_allows_mock_for_test_fixtures():
    response = failing_router(EnvironmentProfile.TEST).invoke(ModelRequest(prompt='test'))
    assert response.is_mock is True


def test_allowed_providers_still_restrict_explicit_preference():
    router = failing_router()
    with pytest.raises(ProviderError):
        router.invoke(ModelRequest(prompt='test', preferred_provider=ProviderName.MOCK,
                                   allowed_providers=[ProviderName.GEMINI]))
