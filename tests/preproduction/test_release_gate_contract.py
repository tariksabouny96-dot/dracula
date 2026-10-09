from pathlib import Path
import ast
import json
from services.capabilities.registry import get_capability_inventory


def test_preprod_inventory_complete_and_truthful():
    root = Path(__file__).resolve().parents[2]
    source = (root / 'scripts/preproduction_gate.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    requirements = next(node.value for node in tree.body
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'LIVE_REQUIREMENTS' for t in node.targets))
    declared = ast.literal_eval(requirements)
    actual = {x['id'] for x in get_capability_inventory()['items']}
    assert set(declared) == actual
    assert 'return 1' in source
    assert "'decision': 'NO_GO'" in source
