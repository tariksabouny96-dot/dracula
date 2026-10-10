"""NOVA 2.3 deterministic local execution: no model, shell, network, or desktop."""
import hashlib
import json
import pytest
from services.operations.mission_service import MissionService, MissionConflict
from tests.hardening.test_remediation import ui_server, request


def test_local_workflow_requires_plan_and_is_owner_scoped(tmp_path):
    s = MissionService(tmp_path)
    m = s.create('owner', 'Check local mission', 'Plan a dummy objective in an isolated directory')
    with pytest.raises(MissionConflict):
        s.run_local_workflow('owner', m['id'])
    s.approve_and_create_plan('owner', m['id'])
    with pytest.raises(KeyError):
        s.run_local_workflow('stranger', m['id'])
    result = s.run_local_workflow('owner', m['id'])
    execution = result['local_execution']
    assert execution['status'] == 'LOCAL_WORKFLOW_COMPLETED'
    assert execution['receipt']['objective_completed'] is False
    assert execution['receipt']['tasks_completed'] == 3
    artifact = s.execution_artifact('owner', m['id'])
    assert hashlib.sha256(artifact).hexdigest() == execution['receipt']['sha256']
    manifest = json.loads(artifact)
    assert [t['status'] for t in manifest['tasks']] == ['COMPLETED'] * 3
    assert manifest['objective_completed'] is False
    assert MissionService(tmp_path).get('owner', m['id'])['local_execution'] == execution
    with pytest.raises(MissionConflict):
        s.run_local_workflow('owner', m['id'])
    with pytest.raises(KeyError):
        s.execution_artifact('stranger', m['id'])


def test_integrity_failure_and_existing_manifest_preservation(tmp_path):
    s = MissionService(tmp_path)
    m = s.create('owner', 'Do local tests', 'Generate isolated test evidence only, no external action')
    s.approve_and_create_plan('owner', m['id'])
    path = s.artifacts / (m['id'] + '.execution.json')
    path.write_text('DO NOT OVERWRITE')
    with pytest.raises(MissionConflict):
        s.run_local_workflow('owner', m['id'])
    assert path.read_text() == 'DO NOT OVERWRITE'
    path.unlink()
    s.run_local_workflow('owner', m['id'])
    path.write_text('tampered')
    with pytest.raises(MissionConflict):
        s.execution_artifact('owner', m['id'])


def test_http_explicit_confirmation_and_permissions(ui_server, tmp_path):
    from ui.server import JarvisUIHandler
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'missions-v23')
    assert request(base, '/api/operations/execute', owner, {'mission_id': 'msn_fake'})[0] == 400
    assert request(base, '/api/operations/execute', viewer, {'mission_id': 'msn_fake', 'confirm': True})[0] == 403
    req = {'title': 'Local tasks test', 'objective': 'Test bounded evidence creation in a dummy workspace'}
    status, body, _ = request(base, '/api/operations/create', owner, req)
    assert status == 201
    mid = json.loads(body)['id']
    assert request(base, '/api/operations/execute', owner, {'mission_id': mid, 'confirm': True})[0] == 409
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    assert request(base, '/api/operations/execute', owner, {'mission_id': mid})[0] == 400
    status, body, _ = request(base, '/api/operations/execute', owner, {'mission_id': mid, 'confirm': True})
    assert status == 200 and json.loads(body)['local_execution']['receipt']['verified']
    assert request(base, '/api/operations/execute', owner, {'mission_id': mid, 'confirm': True})[0] == 409
    status, content, _ = request(base, '/api/operations/execution-artifact/' + mid, owner)
    assert status == 200 and json.loads(content)['objective_completed'] is False
    assert request(base, '/api/operations/execution-artifact/' + mid, viewer)[0] == 403
    assert request(base, '/api/operations/execution-artifact/' + mid)[0] == 401
