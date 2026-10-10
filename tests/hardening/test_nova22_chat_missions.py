"""NOVA 2.2: explicit, owner-scoped chat handoff and durable step receipts."""
import json
import hashlib
from services.operations.mission_service import MissionService
from tests.hardening.test_remediation import ui_server, request
from ui.server import JarvisUIHandler


def test_steps_survive_restart_and_are_not_execution_receipts(tmp_path):
    service = MissionService(tmp_path)
    mission = service.create('owner', 'Safe test mission', 'Generate a local development plan with fake data')
    assert len(mission['steps']) == 4
    assert all(step['status'] != 'COMPLETED' for step in mission['steps'])
    assert not mission['receipt']
    assert MissionService(tmp_path).get('owner', mission['id'])['steps'] == mission['steps']
    finished = service.approve_and_create_plan('owner', mission['id'])
    assert finished['status'] == 'PLAN_CREATED'
    assert len(finished['steps']) == 4
    assert all(step['status'] == 'COMPLETED' for step in finished['steps'])
    assert finished['receipt']['sha256'] == hashlib.sha256(service.artifact('owner', mission['id'])).hexdigest()
    assert finished['external_actions'] is False
    assert MissionService(tmp_path).get('owner', mission['id'])['steps'] == finished['steps']


def test_chat_mission_handoff_requires_owner_and_explicit_command(ui_server, tmp_path):
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'handoff')
    command = '/mission Draft a mock website plan without external actions'
    assert request(base, '/api/chat', None, {'text': command})[0] == 401
    assert request(base, '/api/chat', viewer, {'text': command})[0] == 403
    assert request(base, '/api/chat', owner, {'text': '/mission short'})[0] == 400
    assert JarvisUIHandler.mission_service.list('owner') == []
    code, body, _ = request(base, '/api/chat', owner, {'text': command})
    assert code == 201
    message = json.loads(body)
    assert message['mission_id'].startswith('msn_')
    assert message['mission_status'] == 'AWAITING_APPROVAL'
    assert 'Nothing has been executed' in message['text']
    assert len(json.loads(request(base, '/api/operations', owner)[1])) == 1
    item = json.loads(request(base, '/api/operations', owner)[1])[0]
    assert item['receipt'] is None
    assert item['external_actions'] is False
    assert all(step['status'] != 'COMPLETED' for step in item['steps'])
    assert request(base, '/api/operations/approve', owner, {'mission_id': item['id']})[0] == 400


def test_cancel_updates_step_state(tmp_path):
    service = MissionService(tmp_path)
    created = service.create('owner', 'Plan cancellation', 'Write a reversible dummy mission plan')
    cancelled = service.cancel('owner', created['id'])
    assert cancelled['status'] == 'CANCELLED'
    assert all(step['status'] == 'CANCELLED' for step in cancelled['steps'])
