"""No network access: verify opt-in and provider response contracts."""
import io
import json
import urllib.error
import pytest
from packages.contracts import ModelRequest, ModelClass, ProviderName
from services.model_gateway.openai_adapter import OpenAIProviderAdapter
from services.model_gateway.local_adapter import LocalProviderAdapter
from services.model_gateway.base import ProviderNotConfiguredError, ProviderError


class DummyVault:
    def get_secret(self, ref):
        return 'test-only-key'


class Response:
    def __init__(self, data):
        self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *_args): return json.dumps(self.data).encode()


def test_openai_requires_explicit_network_flag(monkeypatch):
    monkeypatch.delenv('HOOD_ALLOW_OPENAI_API_CALLS', raising=False)
    adapter = OpenAIProviderAdapter(vault=DummyVault(), enabled=True)
    with pytest.raises(ProviderNotConfiguredError):
        adapter.invoke(ModelRequest(prompt='hello'))


def test_openai_real_wire_contract(monkeypatch):
    monkeypatch.setenv('HOOD_ALLOW_OPENAI_API_CALLS', '1')
    monkeypatch.setenv('HOOD_OPENAI_STANDARD_MODEL', 'test-model')
    captured = {}
    def fake_urlopen(req, timeout):
        captured['url'] = req.full_url
        captured['headers'] = dict(req.header_items())
        captured['payload'] = json.loads(req.data)
        return Response({'status': 'completed', 'output': [{'type':'message','content':[{'type':'output_text','text':'review complete'}]}], 'usage': {'input_tokens': 5, 'output_tokens': 6}})
    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)
    result = OpenAIProviderAdapter(vault=DummyVault(), enabled=True).invoke(ModelRequest(prompt='check', system_prompt='safety'))
    assert captured['url'] == 'https://api.openai.com/v1/responses'
    assert captured['payload']['store'] is False
    assert captured['payload']['instructions'] == 'safety'
    assert captured['headers']['Authorization'] == 'Bearer test-only-key'
    assert result.provider == ProviderName.OPENAI and not result.is_mock
    assert result.text == 'review complete' and result.usage.total_tokens == 11


def test_openai_rejects_empty_or_incomplete(monkeypatch):
    monkeypatch.setenv('HOOD_ALLOW_OPENAI_API_CALLS','1')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: Response({'status':'incomplete', 'output': []}))
    with pytest.raises(ProviderError):
        OpenAIProviderAdapter(vault=DummyVault(),enabled=True).invoke(ModelRequest(prompt='test'))


def test_local_model_refuses_non_loopback_endpoint():
    for url in ('http://example.com/v1/chat/completions', 'http://127.0.0.1.evil.test/v1/chat/completions', 'https://localhost:443/v1/chat/completions'):
        with pytest.raises(ProviderNotConfiguredError):
            LocalProviderAdapter(endpoint_url=url)


def test_local_model_retains_system_prompt(monkeypatch):
    captured = {}
    class DummyOpener:
        def open(self, req, timeout):
            captured['payload'] = json.loads(req.data)
            return Response({'choices':[{'message':{'content':'ok'}}], 'usage': {}})
    monkeypatch.setattr('urllib.request.build_opener', lambda *args: DummyOpener())
    local = LocalProviderAdapter()
    answer = local.invoke(ModelRequest(prompt='task', system_prompt='boundaries'))
    assert captured['payload']['messages'] == [{'role':'system','content':'boundaries'}, {'role':'user','content':'task'}]
    assert answer.text == 'ok'
