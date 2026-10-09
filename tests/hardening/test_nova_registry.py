"""NOVA registry regression checks: no fabricated live availability."""
from pathlib import Path
from types import SimpleNamespace
from services.capabilities.registry import CAPABILITIES, get_capability_inventory


def test_inventory_contains_all_distinct_capabilities():
    data = get_capability_inventory()
    items = data['items']
    assert len(items) >= 25
    assert len({i['id'] for i in items}) == len(items)
    assert len({i['name'] for i in items}) == len(items)
    assert sum(data['counts'].values()) == len(items)
    assert all(i['status'] != 'VERIFIED' for i in items)


def test_source_presence_does_not_claim_live_connection():
    by_id = {i['id']: i for i in get_capability_inventory()['items']}
    assert by_id['orchestrator']['status'] == 'MODULE_ONLY'
    assert by_id['openai']['status'] == 'MODULE_ONLY'
    assert by_id['voice']['status'] == 'PLACEHOLDER'
    assert by_id['freelance']['status'] == 'PLACEHOLDER'
    assert by_id['gemini']['status'] != 'ATTACHED_UNVERIFIED'


def test_actual_attached_subsystems_are_still_unverified():
    interaction = SimpleNamespace(commander=object(), approval_service=object(), memory_service=object())
    data = get_capability_inventory(interaction=interaction, sentinel=object(), x_manager=object())
    by_id = {i['id']: i for i in data['items']}
    for key in ('conversation', 'orchestrator', 'governance', 'memory', 'sentinel', 'x'):
        assert by_id[key]['status'] == 'ATTACHED_UNVERIFIED'


def test_every_navigable_section_exists_in_integrated_ui():
    html = (Path(__file__).resolve().parents[2] / 'ui/static/index.html').read_text()
    for cap in get_capability_inventory()['items']:
        if cap['action'] == 'OPEN_SECTION':
            assert f'id="{cap["section"]}"' in html
    assert 'id="tab-systems"' in html
    assert 'id="nova-capability-grid"' in html


def test_registry_route_uses_authz_gate():
    source = (Path(__file__).resolve().parents[2] / 'ui/server.py').read_text()
    assert '"/api/capabilities"):' in source
    assert 'permission = UserPermission.VIEW_PROJECT_DATA' in source
    assert 'self._require_permission(curr_session, permission)' in source
