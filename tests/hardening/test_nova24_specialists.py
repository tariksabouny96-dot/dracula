"""NOVA 2.4 local specialist verification, owner confinement, and HTTP governance."""
import hashlib
import json
import pytest
from services.operations.mission_service import MissionService, MissionConflict
from services.operations.specialist_engine import review
from tests.hardening.test_remediation import ui_server, request


def test_independent_local_review_not_fake_autonomy(tmp_path):
    s = MissionService(tmp_path)
    m = s.create('owner', 'Website planning', 'Build and deploy a test website; send client an invoice')
    with pytest.raises(MissionConflict):
        s.run_specialist_review('owner', m['id'])
    s.approve_and_create_plan('owner', m['id'])
    with pytest.raises(KeyError):
        s.run_specialist_review('other', m['id'])
    result = s.run_specialist_review('owner', m['id'])
    report = json.loads(s.specialist_artifact('owner', m['id']))
    assert report['verification']['checks'] == [True, True]
    assert report['objective_completed'] is False
    assert report['llm_agents_executed'] is False
    assert report['external_actions'] is False
    assert 'financial_action' in report['specialists'][1]['result']['signals']
    assert result['specialist_review']['receipt']['verified'] is True
    assert MissionService(tmp_path).get('owner', m['id'])['specialist_review'] == result['specialist_review']
    with pytest.raises(MissionConflict):
        s.run_specialist_review('owner', m['id'])
    with pytest.raises(KeyError):
        s.specialist_artifact('other', m['id'])


def test_integrity_and_nonoverwrite(tmp_path):
    s = MissionService(tmp_path)
    m = s.create('owner', 'Plan architecture', 'Design a local data model for demonstration')
    s.approve_and_create_plan('owner', m['id'])
    target = s.artifacts / (m['id'] + '.specialists.json')
    target.write_text('keep this artifact')
    with pytest.raises(MissionConflict):
        s.run_specialist_review('owner', m['id'])
    assert target.read_text() == 'keep this artifact'
    target.unlink()
    s.run_specialist_review('owner', m['id'])
    target.write_text('tampered')
    with pytest.raises(MissionConflict):
        s.specialist_artifact('owner', m['id'])


def test_verifier_does_not_trust_self_attested_hash(monkeypatch):
    from services.operations import specialist_engine as eng
    original = eng.deliverables
    calls = [0]
    def shifting(text):
        calls[0] += 1
        return {'result': calls[0]}
    replacement = eng.Specialist('unstable', '1', shifting)
    monkeypatch.setattr(eng, 'SPECIALISTS', (replacement,))
    with pytest.raises(ValueError, match='verification failed'):
        review('A sufficiently long objective for the mission', 'a' * 64)


def test_http_specialist_approval_owner_and_artifact(ui_server, tmp_path):
    from ui.server import JarvisUIHandler
    base, owner, viewer, _ = ui_server
    JarvisUIHandler.mission_service = MissionService(tmp_path / 'nova24')
    status, body, _ = request(base, '/api/operations/create', owner, {
        'title': 'Dummy local analysis', 'objective': 'Plan the website launch and invoice the dummy client'})
    assert status == 201
    mid = json.loads(body)['id']
    assert request(base, '/api/operations/analyze', owner, {'mission_id': mid})[0] == 400
    assert request(base, '/api/operations/analyze', viewer, {'mission_id': mid, 'confirm': True})[0] == 403
    assert request(base, '/api/operations/analyze', owner, {'mission_id': mid, 'confirm': True})[0] == 409
    assert request(base, '/api/operations/approve', owner, {'mission_id': mid, 'confirm': True})[0] == 200
    status, body, _ = request(base, '/api/operations/analyze', owner, {'mission_id': mid, 'confirm': True})
    assert status == 200 and json.loads(body)['specialist_review']['receipt']['verified'] is True
    assert request(base, '/api/operations/analyze', owner, {'mission_id': mid, 'confirm': True})[0] == 409
    status, body, _ = request(base, '/api/operations/specialist-artifact/' + mid, owner)
    assert status == 200 and not json.loads(body)['llm_agents_executed']
    assert request(base, '/api/operations/specialist-artifact/' + mid, viewer)[0] == 403
    assert request(base, '/api/operations/specialist-artifact/' + mid)[0] == 401
