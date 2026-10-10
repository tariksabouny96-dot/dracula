"""NOVA 2.1 durable planning workflow regression tests; no external side effects."""
import hashlib
import pytest
from tests.hardening.test_remediation import ui_server
from services.operations.mission_service import MissionService, MissionConflict


def test_owner_scope_restart_and_artifact(tmp_path):
    service = MissionService(tmp_path)
    mission = service.create('owner_a', 'Build storefront', 'Create a responsive storefront specification')
    assert mission['status'] == 'AWAITING_APPROVAL'
    assert service.list('owner_b') == []
    with pytest.raises(KeyError):
        service.approve_and_create_plan('owner_b', mission['id'])
    assert service.list('owner_a')[0]['status'] == 'AWAITING_APPROVAL'
    with pytest.raises(MissionConflict):
        service.artifact('owner_a', mission['id'])
    result = service.approve_and_create_plan('owner_a', mission['id'])
    assert result['status'] == 'PLAN_CREATED'
    assert result['execution_mode'] == 'LOCAL_PLAN_ARTIFACT_ONLY'
    assert result['external_actions'] is False
    data = service.artifact('owner_a', mission['id'])
    assert hashlib.sha256(data).hexdigest() == result['receipt']['sha256']
    assert result['receipt']['verified'] is True
    assert b'No agents, browsers, commands' in data
    with pytest.raises(MissionConflict):
        service.approve_and_create_plan('owner_a', mission['id'])
    assert MissionService(tmp_path).get('owner_a', mission['id'])['status'] == 'PLAN_CREATED'


def test_cancellation_and_input_validation(tmp_path):
    service = MissionService(tmp_path)
    with pytest.raises(ValueError):
        service.create('owner', 'x', 'too short')
    mission = service.create('owner', 'Create proposal', 'A detailed proposal for a dummy project')
    with pytest.raises(KeyError):
        service.cancel('intruder', mission['id'])
    assert service.cancel('owner', mission['id'])['status'] == 'CANCELLED'
    with pytest.raises(MissionConflict):
        service.approve_and_create_plan('owner', mission['id'])


def test_integrity_and_confinement(tmp_path):
    service = MissionService(tmp_path)
    mission = service.create('owner', 'Review notes', 'Draft some dummy notes in a local planning file')
    service.approve_and_create_plan('owner', mission['id'])
    (service.artifacts / (mission['id'] + '.md')).write_text('tampered')
    with pytest.raises(MissionConflict):
        service.artifact('owner', mission['id'])


def test_existing_artifact_must_not_be_overwritten(tmp_path):
    service = MissionService(tmp_path)
    mission = service.create('owner', 'Review notes', 'Draft some dummy notes in a local planning file')
    external = tmp_path / 'do-not-touch'
    external.write_text('keep this')
    (service.artifacts / (mission['id'] + '.md')).symlink_to(external)
    with pytest.raises(MissionConflict):
        service.approve_and_create_plan('owner', mission['id'])
    assert external.read_text() == 'keep this'
    assert service.get('owner', mission['id'])['status'] == 'AWAITING_APPROVAL'


def test_http_mission_flow_is_owner_only_and_explicit(ui_server, tmp_path):
    from ui.server import JarvisUIHandler
    from tests.hardening.test_remediation import request
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'httpmissions')
    assert request(base, '/api/operations')[0] == 401
    assert request(base, '/api/operations', viewer)[0] == 403
    req = {'title': 'Local research plan', 'objective': 'Draft a safe local plan without external calls'}
    assert request(base, '/api/operations/create', viewer, req)[0] == 403
    import json
    status, raw, _ = request(base, '/api/operations/create', owner, req)
    assert status == 201
    mid = json.loads(raw)['id']
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid})[0] == 400
    assert request(base, '/api/operations/artifact/' + mid, owner)[0] == 409
    status, raw, _ = request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})
    assert status == 200 and json.loads(raw)['status'] == 'PLAN_CREATED'
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})[0] == 409
    status, artifact, _ = request(base, '/api/operations/artifact/' + mid, owner)
    assert status == 200 and b'LOCAL_PLAN_ARTIFACT_ONLY' in artifact
    assert request(base, '/api/operations/artifact/' + mid, viewer)[0] == 403
