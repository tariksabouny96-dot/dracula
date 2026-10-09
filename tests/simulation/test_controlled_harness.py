import json
import pytest
from services.simulation.harness import ControlledSimulation, demonstration


def test_simulation_is_explicit_and_contains_no_real_operations():
    report = demonstration()
    assert report['mode'] == 'CONTROLLED_SIMULATION'
    assert report['real_operations_executed'] == 0
    assert report['production_acceptance'] is False
    assert all(event['simulated'] and event['status'] == 'SIMULATED' for event in report['events'])
    assert {event['component'] for event in report['events']} == {'voice', 'browser', 'provider', 'desktop'}
    assert report['emergency_stop_enforced']


def test_consequential_action_requires_exact_one_time_approval():
    sim = ControlledSimulation()
    params = {'text': 'dummy'}
    with pytest.raises(PermissionError):
        sim.execute('desktop', 'type', params, consequential=True)
    sim.authorize('a', component='desktop', operation='type', parameters=params)
    with pytest.raises(PermissionError):
        sim.execute('desktop', 'type', {'text': 'different'}, consequential=True, action_id='a')
    assert sim.execute('desktop', 'type', params, consequential=True, action_id='a').status == 'SIMULATED'
    with pytest.raises(PermissionError):
        sim.execute('desktop', 'type', params, consequential=True, action_id='a')


def test_emergency_stop_revokes_approvals_and_halts_operations():
    sim = ControlledSimulation()
    sim.authorize('a', component='browser', operation='submit', parameters={})
    sim.emergency_stop()
    assert sim.approved == {}
    with pytest.raises(PermissionError):
        sim.execute('browser', 'submit', {}, action_id='a', consequential=True)
    with pytest.raises(PermissionError):
        sim.authorize('another', component='browser', operation='submit', parameters={})


def test_simulation_receipt_stable_and_machine_readable():
    sim = ControlledSimulation()
    sim.execute('voice', 'listen', {'fixture': 1})
    report = sim.report()
    assert len(report['event_log_sha256']) == 64
    assert json.loads(json.dumps(report)) == report
