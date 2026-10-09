"""Regression tests: failed inspections and unsupported repairs must not claim success."""
import subprocess
from unittest.mock import patch

from services.sentinel.firewall import FirewallManager
from services.sentinel.self_healing import SelfHealingEngine
from services.sentinel.sentinel_service import SecuritySentinelService


def test_failed_firewall_probe_is_not_marked_active():
    with patch('services.sentinel.firewall.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'', b'failed')):
        status = FirewallManager().inspect_firewall_status()
    assert not status.is_active
    assert status.profiles[0].profile_name == 'InspectionError'
    assert 'exit status' in status.profiles[0].state_raw


def test_unparseable_firewall_output_is_not_marked_active():
    with patch('services.sentinel.firewall.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'unrecognized text', b'')):
        status = FirewallManager().inspect_firewall_status()
    assert not status.is_active
    assert status.profiles[0].profile_name == 'InspectionError'


def test_cache_path_outside_approved_root_is_not_deleted(tmp_path):
    victim = tmp_path / 'unrelated'
    victim.mkdir()
    (victim / 'keep').write_text('preserve')
    action = SelfHealingEngine(actions_log=tmp_path / 'actions.log').repair_disposable_cache(victim)
    assert not action.success and not action.executed and not action.is_authorized
    assert (victim / 'keep').read_text() == 'preserve'


def test_sentinel_posture_cannot_be_optimal_without_firewall_evidence(tmp_path):
    service = SecuritySentinelService(registry_path=tmp_path / 'registry.json')
    with patch.object(service.firewall_manager, 'inspect_firewall_status') as inspected:
        inspected.return_value.is_active = False
        inspected.return_value.listening_ports = []
        inspected.return_value.baseline_deviations = []
        result = service.get_security_summary()
    assert result['posture'] == 'UNVERIFIED FIREWALL STATE'
