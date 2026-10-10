"""P0/P1 agent runtime regression tests; no real provider, shell or browser calls."""
import json
import pytest
from services.operations.mission_service import MissionService
from services.operations.agent_runtime import LocalAgentRuntime, AgentRuntimeConflict
from tests.hardening.test_remediation import ui_server, request


def ready(tmp_path):
    svc = MissionService(tmp_path)
    mid = svc.create('owner', 'Example website', 'Build a simple local portfolio for a hypothetical client')['id']
    svc.approve_and_create_plan('owner', mid)
    return svc, mid


def test_durable_agent_graph_and_verification(tmp_path):
    svc, mid = ready(tmp_path)
    engine = LocalAgentRuntime(svc)
    assert engine.start('owner', mid)['state'] == 'READY'
    with pytest.raises(AgentRuntimeConflict):
        engine.start('owner', mid)
    with pytest.raises(KeyError):
        engine.advance('other', mid)
    states = []
    for _ in range(3):
        states.append(LocalAgentRuntime(MissionService(tmp_path)).advance('owner', mid)['state'])
    assert states == ['RUNNING', 'RUNNING', 'COMPLETED']
    graph = engine.status('owner', mid)
    assert [t['status'] for t in graph['tasks']] == ['COMPLETED'] * 3
    assert graph['objective_completed'] is False
    assert (tmp_path / 'agent_runtime' / mid / 'preview.html').exists()
    with pytest.raises(AgentRuntimeConflict):
        engine.advance('owner', mid)


def test_tamper_fail_closed_and_no_unapproved_retry(tmp_path):
    svc, mid = ready(tmp_path)
    engine = LocalAgentRuntime(svc)
    engine.start('owner', mid)
    engine.advance('owner', mid)
    engine.advance('owner', mid)
    page = tmp_path / 'agent_runtime' / mid / 'preview.html'
    page.write_text('modified after build')
    with pytest.raises(AgentRuntimeConflict, match='integrity'):
        engine.advance('owner', mid)
    assert engine.status('owner', mid)['state'] == 'BLOCKED'
    with pytest.raises(AgentRuntimeConflict):
        engine.advance('owner', mid)


def test_cancel_and_crash_reconciliation(tmp_path):
    svc, mid = ready(tmp_path)
    engine = LocalAgentRuntime(svc)
    engine.start('owner', mid)
    assert engine.cancel('owner', mid)['state'] == 'CANCELLED'
    with pytest.raises(AgentRuntimeConflict):
        engine.advance('owner', mid)
    svc2, mid2 = ready(tmp_path / 'second')
    e2 = LocalAgentRuntime(svc2)
    e2.start('owner', mid2)
    with e2._db() as db:
        db.execute("UPDATE agent_tasks SET status='RUNNING' WHERE mission_id=? AND task_id='requirements'", (mid2,))
    assert LocalAgentRuntime(MissionService(tmp_path / 'second')).reconcile('owner', mid2)['state'] == 'BLOCKED'


def test_escapes_html_and_workspace_symlink(tmp_path):
    svc = MissionService(tmp_path)
    mid = svc.create('owner', 'Site <script>', 'Create a page showing <script>alert(1)</script> safely')['id']
    svc.approve_and_create_plan('owner', mid)
    e = LocalAgentRuntime(svc)
    e.start('owner', mid)
    e.advance('owner', mid)
    e.advance('owner', mid)
    page = (tmp_path / 'agent_runtime' / mid / 'preview.html').read_text()
    assert '<script>' not in page and '&lt;script&gt;' in page
    assert e.advance('owner', mid)['state'] == 'COMPLETED'


def test_http_requires_owner_and_explicit_consent(ui_server, tmp_path):
    from ui.server import JarvisUIHandler
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'http')
    status, raw, _ = request(base, '/api/operations/create', owner, {
        'title': 'Test safe agents', 'objective': 'Create isolated sample output from approved local mission'})
    assert status == 201
    mid = json.loads(raw)['id']
    assert request(base, '/api/operations/agent-start', owner, {'mission_id': mid, 'confirm': True})[0] == 409
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    assert request(base, '/api/operations/agent-start', owner, {'mission_id': mid})[0] == 400
    assert request(base, '/api/operations/agent-start', viewer, {'mission_id': mid, 'confirm': True})[0] == 403
    assert request(base, '/api/operations/agent-start', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    for _ in range(3):
        assert request(base, '/api/operations/agent-step', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    assert request(base, '/api/operations/agent-status/' + mid, viewer)[0] == 403
    assert json.loads(request(base, '/api/operations/agent-status/' + mid, owner)[1])['state'] == 'COMPLETED'


def test_preview_is_gated_and_verifies_content(tmp_path):
    svc, mid = ready(tmp_path)
    e = LocalAgentRuntime(svc)
    e.start('owner', mid)
    with pytest.raises(AgentRuntimeConflict):
        e.preview('owner', mid)
    for _ in range(3):
        e.advance('owner', mid)
    assert b'HOOD local prototype' in e.preview('owner', mid)
    with pytest.raises(KeyError):
        e.preview('intruder', mid)
    page = tmp_path / 'agent_runtime' / mid / 'preview.html'
    page.write_text('different')
    with pytest.raises(AgentRuntimeConflict, match='changed since QA'):
        e.preview('owner', mid)


def test_cancelled_in_flight_task_cannot_commit(tmp_path, monkeypatch):
    svc, mid = ready(tmp_path)
    e = LocalAgentRuntime(svc)
    e.start('owner', mid)
    original = e._execute
    def cancel_during_execution(action, mission, digest):
        e.cancel('owner', mid)
        return original(action, mission, digest)
    monkeypatch.setattr(e, '_execute', cancel_during_execution)
    with pytest.raises(AgentRuntimeConflict, match='cancelled'):
        e.advance('owner', mid)
    result = e.status('owner', mid)
    assert result['state'] == 'CANCELLED'
    assert result['tasks'][0]['status'] != 'COMPLETED'


def test_http_preview_is_owner_only(ui_server, tmp_path):
    from ui.server import JarvisUIHandler
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'browser')
    status, raw, _ = request(base, '/api/operations/create', owner, {
        'title': 'HTML preview', 'objective': 'Create a safe isolated HTML file and verify its content'})
    assert status == 201
    mid = json.loads(raw)['id']
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    assert request(base, '/api/operations/agent-preview/' + mid, owner)[0] == 409
    assert request(base, '/api/operations/agent-preview/' + mid, viewer)[0] == 403
    assert request(base, '/api/operations/agent-start', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    for _ in range(3):
        assert request(base, '/api/operations/agent-step', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    status, body, headers = request(base, '/api/operations/agent-preview/' + mid, owner)
    assert status == 200 and body.startswith(b'<!doctype html>')
    assert request(base, '/api/operations/agent-preview/' + mid)[0] == 401
