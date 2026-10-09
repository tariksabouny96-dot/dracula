"""No release UI may claim evidence it has not collected."""
from pathlib import Path
from services.capabilities.registry import get_capability_inventory
from ui.server import JarvisUIHandler


def test_configured_adapter_not_claimed_operational():
    items = {i['id']: i for i in get_capability_inventory()['items']}
    assert items['openai']['source_present']
    assert items['openai']['status'] == 'MODULE_ONLY'
    assert items['hood_model']['status'] == 'PLACEHOLDER'


def test_unattached_telemetry_is_not_made_up():
    handler = object.__new__(JarvisUIHandler)
    handler.interaction_service = None
    handler.runtime = None
    handler.sentinel_service = None
    handler.x_session_manager = None
    data = handler._get_live_telemetry()
    assert 'not independently measured' in data['desktop']['financial_spend'].lower()
    assert data['sentinel']['posture'] == 'NOT INSPECTED'
    assert data['sentinel']['firewall']['active'] is None
    assert data['v1_2']['frontier_gap'] is None
    assert 'UNVERIFIED' in data['nodes']['badge']
