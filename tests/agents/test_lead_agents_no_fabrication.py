"""F09 regression: legacy domain leads must not fabricate success verdicts.

Every lead returns model *reasoning* that is explicitly UNVERIFIED, with no
hardcoded PASS/VALIDATED/OPTIMAL verdict, no fabricated primary evidence, no
invented tool use, and modest confidence. Simulated (mock) output is labelled.
"""

import pytest

from packages.config import load_config, EnvironmentProfile
from packages.contracts import TaskNode
from services.model_gateway.router import ModelRouter
from services.model_gateway.cost_controller import CostController
from services.core.agents.lead_agents import LeadAgentRegistry

FABRICATED_VERDICTS = [
    "PASSED", "VALIDATED", "OPTIMAL", "Completed within budget",
    "Evidence-backed synthesis assembled",
]


@pytest.fixture
def registry():
    config = load_config()
    # TEST profile authorises the MOCK provider fallback for an offline run.
    config.environment = EnvironmentProfile.TEST
    router = ModelRouter(config, CostController(config.budgets))
    return LeadAgentRegistry(router)


def test_no_lead_fabricates_a_verdict_or_primary_evidence(registry):
    task = TaskNode(title="Do the thing", objective="Accomplish the objective safely")
    for lead in registry._leads.values():
        resp = lead.execute(task)

        # Result is honest analysis, never a stamped success verdict.
        assert isinstance(resp.result, dict)
        assert resp.result.get("verdict") == "UNVERIFIED"
        blob = str(resp.result)
        for forbidden in FABRICATED_VERDICTS:
            assert forbidden not in blob, f"{lead.name} fabricated verdict: {forbidden}"

        # Reasoning is not a verified postcondition.
        assert resp.confidence <= 0.5
        assert resp.tools_used == []
        assert all(not e.is_primary for e in resp.evidence)

        # Mock output must be labelled simulated, never passed off as live.
        assert resp.result.get("simulated") is True
