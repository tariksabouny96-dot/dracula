import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from services.operations.llm_specialists import run, parse_output
from services.operations.mission_service import MissionService, MissionConflict
from tests.hardening.test_remediation import ui_server


def fake(request):
    assert request.agent in ('delivery_architect', 'risk_analyst')
    return SimpleNamespace(is_mock=False, provider='gemini', model_name='gemini-test-stub',
                           text=json.dumps({'summary': 'Advisory plan', 'recommendations': ['Review scope'], 'risks': []}),
                           usage=SimpleNamespace(total_tokens=20))


def test_schema_rejects_invalid_or_unexpected_data():
    for bad in ('not-json', '{}', '{"summary":"x","recommendations":[],"risks":[],"approved":true}',
                '{"summary":"x","recommendations":"yes","risks":[]}'):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            parse_output(bad)


def test_two_actual_provider_adapter_contract_calls():
    calls = []
    def invoke(req):
        calls.append(req.agent)
        return fake(req)
    result = run('Build a small project with review', invoke=invoke)
    assert calls == ['delivery_architect', 'risk_analyst']
    assert result['objective_completed'] is False
    assert result['validation'].startswith('STRICT_SCHEMA')


def test_rejects_mock_output():
    def simulated(request):
        output = fake(request)
        output.is_mock = True
        return output
    with pytest.raises(ValueError, match='Simulated'):
        run('Build a small project with review', invoke=simulated)


def test_mission_owner_scope_replay_tamper_and_failure(tmp_path):
    store = MissionService(tmp_path)
    item = store.create('owner', 'Website', 'Build an accessible sample website')
    mid = item['id']
    store.approve_and_create_plan('owner', mid)
    with pytest.raises(KeyError):
        store.run_llm_review('intruder', mid, invoke=fake)
    def failed(request):
        raise RuntimeError('provider offline')
    with pytest.raises(RuntimeError):
        store.run_llm_review('owner', mid, invoke=failed)
    assert store.get('owner', mid)['llm_review'] is None
    out = store.run_llm_review('owner', mid, invoke=fake)
    assert out['llm_review']['status'] == 'ADVISORY_COMPLETED'
    assert json.loads(store.llm_artifact('owner', mid))['agent_count'] == 2
    with pytest.raises(MissionConflict):
        store.run_llm_review('owner', mid, invoke=fake)
    with pytest.raises(KeyError):
        store.llm_artifact('intruder', mid)
    (tmp_path / 'artifacts' / (mid + '.llm.json')).write_text('tampered')
    with pytest.raises(MissionConflict):
        store.llm_artifact('owner', mid)


def test_http_opt_in_and_owner_permissions(ui_server, tmp_path, monkeypatch):
    from ui.server import JarvisUIHandler
    from tests.hardening.test_remediation import request
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'http_llm')
    status, body, _ = request(base, '/api/operations/create', owner, {
        'title': 'Test website', 'objective': 'Create an accessible website planning report'})
    assert status == 201
    mid = json.loads(body)['id']
    payload = {'mission_id': mid, 'confirm': True, 'acknowledge_network': True}
    assert request(base, '/api/operations/llm-analyze', viewer, payload)[0] == 403
    assert request(base, '/api/operations/llm-analyze', owner, {'mission_id': mid})[0] == 400
    assert request(base, '/api/operations/llm-analyze', owner, payload)[0] == 503
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    monkeypatch.setenv('HOOD_ENABLE_LLM_SPECIALISTS', '1')
    from services.operations import mission_service
    monkeypatch.setattr(mission_service, 'run_llm_agents', lambda objective, invoke=None: run(objective, invoke=fake))
    assert request(base, '/api/operations/llm-analyze', owner, payload)[0] == 200
    status, body, _ = request(base, '/api/operations/llm-artifact/' + mid, owner)
    assert status == 200 and json.loads(body)['mode'] == 'REAL_PROVIDER_ADVISORY'
    assert request(base, '/api/operations/llm-artifact/' + mid, viewer)[0] == 403
    assert request(base, '/api/operations/llm-analyze', owner, payload)[0] == 409
