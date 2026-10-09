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
    with pytest.raises(ProviderError, match='no text'):
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
