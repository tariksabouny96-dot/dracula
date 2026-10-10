import io
import json
from urllib.error import HTTPError
from unittest.mock import Mock
import pytest
from packages.contracts import ModelRequest, ModelClass
from services.model_gateway.gemini_adapter import GeminiProviderAdapter
from services.model_gateway.base import ProviderError

class StubVault:
    def get_secret(self, ref):
        return 'fake-test-key'

class Response:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.data[:limit]

def test_secret_is_header_not_url(monkeypatch):
    def urlopen(req, timeout):
        assert 'fake-test-key' not in req.full_url
        assert req.get_header('X-goog-api-key') == 'fake-test-key'
        return Response({'candidates':[{'content':{'parts':[{'text':'hello'}]}}], 'usageMetadata':{'promptTokenCount':3,'candidatesTokenCount':2}})
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    adapter=GeminiProviderAdapter(vault=StubVault())
    result=adapter.invoke(ModelRequest(prompt='hi'))
    assert result.text == 'hello'
    assert result.usage.total_tokens == 5

def test_model_tiers_are_explicit(monkeypatch):
    adapter=GeminiProviderAdapter(vault=StubVault())
    monkeypatch.setenv('HOOD_GEMINI_DEEP_MODEL','gemini-test-deep')
    assert adapter._select_candidate_models(ModelClass.DEEP)==['gemini-test-deep']
    monkeypatch.setenv('HOOD_GEMINI_DEEP_MODEL','bad/name?key=x')
    with pytest.raises(Exception, match='Invalid Gemini model'):
        adapter._select_candidate_models(ModelClass.DEEP)

def test_missing_output_fails_closed(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda req,timeout: Response({'candidates':[]}))
    with pytest.raises(ProviderError, match='no answer|no text'):
        GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi'))

def test_invalid_auth_fails_closed_without_retries(monkeypatch):
    calls=[]
    def reject(req,timeout):
        calls.append(req)
        raise HTTPError(req.full_url,401,'unauthorized',{},io.BytesIO(b'no access'))
    monkeypatch.setattr('urllib.request.urlopen', reject)
    with pytest.raises(ProviderError, match='401'):
        GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi'))
    assert len(calls)==1


class EmptyVault:
    def get_secret(self, ref):
        return None


def test_proxy_credential_mode_sends_no_key(monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('GOOGLE_API_KEY', raising=False)
    adapter = GeminiProviderAdapter(vault=EmptyVault())
    with pytest.raises(ProviderError, match='not found'):
        adapter.invoke(ModelRequest(prompt='hi'))
    monkeypatch.setenv('HOOD_GEMINI_CREDENTIAL', 'proxy')
    monkeypatch.setenv('HTTPS_PROXY', 'http://key-injecting-proxy.test:3128')   # the mode needs a proxy
    seen = {}
    def urlopen(req, timeout):
        seen['key'] = req.get_header('X-goog-api-key')
        seen['body'] = json.loads(req.data)
        return Response({'candidates': [{'content': {'parts': [{'text': '{}'}]}, 'finishReason': 'STOP'}],
                         'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 2, 'thoughtsTokenCount': 40},
                         'modelVersion': 'gemini-3.8-flash'})
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    result = adapter.invoke(ModelRequest(prompt='hi', response_mime_type='application/json'))
    assert seen['key'] is None, 'the proxy adds the key; Hood must not hold it'
    assert seen['body']['generationConfig']['responseMimeType'] == 'application/json'
    assert result.usage.completion_tokens == 42, 'thinking tokens are billed output tokens'
    assert adapter.is_healthy()


def test_truncated_output_is_an_error(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda req, timeout: Response(
        {'candidates': [{'content': {'parts': [{'text': '{"files": [{"path": "app/x.py", "conte'}]},
                         'finishReason': 'MAX_TOKENS'}]}))
    with pytest.raises(ProviderError, match='truncated'):
        GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi'))


def test_rate_limit_retries_with_server_delay(monkeypatch):
    monkeypatch.setenv('HOOD_GEMINI_FALLBACK_MODELS', '')   # last model available: waiting is the only option
    calls, sleeps = [], []
    def urlopen(req, timeout):
        calls.append(1)
        if len(calls) < 3:
            body = b'{"error": {"details": [{"retryDelay": "7s"}]}}'
            raise HTTPError(req.full_url, 429, 'quota', {}, io.BytesIO(body))
        return Response({'candidates': [{'content': {'parts': [{'text': 'ok'}]}, 'finishReason': 'STOP'}]})
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    monkeypatch.setattr('time.sleep', lambda s: sleeps.append(s))
    assert GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi')).text == 'ok'
    assert sleeps == [7.0, 7.0] and len(calls) == 3


def test_quota_on_primary_switches_to_fallback_without_waiting(monkeypatch):
    """Owner's run: chat stalled ~2 minutes waiting out an exhausted quota."""
    monkeypatch.setenv('HOOD_GEMINI_FALLBACK_MODELS', 'gemini-lite-test')
    sleeps, urls = [], []
    def urlopen(req, timeout):
        urls.append(req.full_url)
        if 'gemini-lite-test' not in req.full_url:
            body = b'{"error": {"message": "You exceeded your current quota", "details": [{"retryDelay": "40s"}]}}'
            raise HTTPError(req.full_url, 429, 'quota', {}, io.BytesIO(body))
        return Response({'candidates': [{'content': {'parts': [{'text': 'ok'}]}, 'finishReason': 'STOP'}]})
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    monkeypatch.setattr('time.sleep', lambda s: sleeps.append(s))
    result = GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi'))
    assert result.model_name == 'gemini-lite-test' and result.is_fallback
    assert sleeps == [] and sum('gemini-lite-test' not in u for u in urls) == 1


def test_daily_quota_is_not_waited_out(monkeypatch):
    monkeypatch.setenv('HOOD_GEMINI_FALLBACK_MODELS', '')
    sleeps = []
    def urlopen(req, timeout):
        body = b'{"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}, {"retryDelay": "30s"}]}}'
        raise HTTPError(req.full_url, 429, 'quota', {}, io.BytesIO(body))
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    monkeypatch.setattr('time.sleep', lambda s: sleeps.append(s))
    with pytest.raises(Exception, match='quota'):
        GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi'))
    assert sleeps == []


def test_overloaded_primary_falls_back_to_listed_model(monkeypatch):
    monkeypatch.setenv('HOOD_GEMINI_FALLBACK_MODELS', 'gemini-lite-test')
    monkeypatch.setattr('time.sleep', lambda s: None)
    urls = []
    def urlopen(req, timeout):
        urls.append(req.full_url)
        if 'gemini-lite-test' not in req.full_url:
            raise HTTPError(req.full_url, 503, 'busy', {}, io.BytesIO(b'{"error": "high demand"}'))
        return Response({'candidates': [{'content': {'parts': [{'text': 'ok'}]}, 'finishReason': 'STOP'}]})
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    result = GeminiProviderAdapter(vault=StubVault()).invoke(ModelRequest(prompt='hi'))
    assert result.is_fallback and result.model_name == 'gemini-lite-test'
    assert sum('gemini-lite-test' not in u for u in urls) == 3  # 2 retries when a fallback exists


def test_router_refuses_when_a_fallback_model_is_unpriced(monkeypatch):
    from packages.config import SystemConfig
    from packages.config.pricing import ModelPrice
    from services.model_gateway.router import ModelRouter
    from packages.contracts import ProviderName
    monkeypatch.setenv('HOOD_GEMINI_FALLBACK_MODELS', 'unpriced-model')
    monkeypatch.setenv('HOOD_GEMINI_STANDARD_MODEL', 'priced-model')
    router = ModelRouter(SystemConfig(), price_table={'gemini': {'priced-model': ModelPrice(0, 0, 'x', 'y')}})
    router.providers[ProviderName.GEMINI] = GeminiProviderAdapter(vault=StubVault())
    assert router.estimate_max_cost(ModelRequest(prompt='hi', allowed_providers=[ProviderName.GEMINI])) == float('inf')
