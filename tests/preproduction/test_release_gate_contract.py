"""Contract tests for the evidence-consuming preproduction gate (scripts/preproduction_gate.py)."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from services.capabilities.registry import get_capability_inventory

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gate", ROOT / "scripts" / "preproduction_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

REGISTER = json.loads((ROOT / "audit" / "CAPABILITY_ACCEPTANCE.json").read_text(encoding="utf-8"))
INVENTORY = {item["id"] for item in get_capability_inventory()["items"]}


def test_register_covers_live_registry_exactly():
    assert {c["id"] for c in REGISTER["capabilities"]} == INVENTORY
    assert len(INVENTORY) == 29
    for cap in REGISTER["capabilities"]:
        assert cap["required_level"] in gate.RANK and cap["local_evidence_level"] in gate.RANK
        assert cap["blockers"] or cap["required_level"] == "DEFERRED" or cap["local_evidence_level"] == cap["required_level"]


def _all_pass_results():
    results = {}
    for cap in REGISTER["capabilities"]:
        for test_id in cap["test_ids"]:
            results[test_id if "::" in test_id else test_id + "::test_example"] = "passed"
    for name in gate.RECOVERY_TESTS:
        results[f"tests/agents/test_recovery.py::{name}"] = "passed"
    results["tests/security/test_x.py::test_y"] = "passed"
    return results


def test_current_evidence_is_no_go_with_reasons():
    findings = [{"id": "F05", "priority": "P0", "status": "MITIGATED_LOCAL"}]
    verdict = gate.decide(REGISTER, INVENTORY, _all_pass_results(), findings, commit="abc", dirty=[], suite_exit=0)
    assert verdict["decision"] == "NO_GO"
    assert verdict["gates"]["G1_p0_security"]["status"] == "FAIL"
    assert verdict["gates"]["G2_functional_core"]["status"] == "FAIL"  # scripted model is not a live LLM
    assert verdict["gates"]["G6_deployment"]["status"] == "FAIL"
    assert verdict["accepted_capabilities"] < verdict["total_capabilities"]


def test_skips_and_failures_never_count_as_pass():
    results = _all_pass_results()
    results.pop("tests/integration/test_real_gemini.py::test_example")
    results["tests/integration/test_real_gemini.py::test_gemini_adapter_live_call"] = "skipped"
    results["tests/agents/test_agent_engine.py::test_golden_journey_catches_planted_bug_repairs_and_delivers"] = "failed"
    verdict = gate.decide(REGISTER, INVENTORY, results, [], commit="abc", dirty=[], suite_exit=1)
    g5 = verdict["gates"]["G5_full_suite"]
    assert g5["status"] == "FAIL" and any("skipped" in r for r in g5["reasons"])
    orch = next(c for c in verdict["capabilities"] if c["id"] == "orchestrator")
    assert orch["achieved_level"] == "FAILED" and not orch["accepted"]
    gem = next(c for c in verdict["capabilities"] if c["id"] == "gemini")
    assert gem["achieved_level"] == "BLOCKED"


def test_dirty_tree_and_register_drift_fail_g0():
    verdict = gate.decide(REGISTER, INVENTORY | {"new_capability"}, _all_pass_results(), [], commit="abc",
                          dirty=[" M services/x.py"], suite_exit=0)
    reasons = " ".join(verdict["gates"]["G0_baseline"]["reasons"])
    assert "not clean" in reasons and "new_capability" in reasons


def test_go_requires_every_gate(tmp_path, monkeypatch):
    """Even a register where everything is accepted stays NO_GO without staging and review evidence."""
    register = copy.deepcopy(REGISTER)
    for cap in register["capabilities"]:
        cap["local_evidence_level"] = "VERIFIED_STAGING"
        cap["owner_deferral_approved"] = True
        if not cap["test_ids"]:
            cap["implementation_status"] = "VERIFIED_STAGING"
    findings = [{"id": "F01", "priority": "P0", "status": "FIXED_VERIFIED_STAGING"}]
    monkeypatch.setattr(gate, "STAGING_EVIDENCE", tmp_path / "staging.json")
    monkeypatch.setattr(gate, "INDEPENDENT_REVIEW", tmp_path / "review.json")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    verdict = gate.decide(register, INVENTORY, _all_pass_results(), findings, commit="c1", dirty=[], suite_exit=0)
    assert verdict["decision"] == "NO_GO"
    (tmp_path / "staging.json").write_text(json.dumps({"commit": "OLD", "deploy": "PASS", "smoke": "PASS",
                                                       "backup_restore": "PASS", "rollback": "PASS"}))
    (tmp_path / "review.json").write_text(json.dumps({"commit": "c1", "security_signoff": "PASS",
                                                      "architecture_signoff": "PASS"}))
    verdict = gate.decide(register, INVENTORY, _all_pass_results(), findings, commit="c1", dirty=[], suite_exit=0)
    assert verdict["decision"] == "NO_GO" and "OLD" in " ".join(verdict["gates"]["G6_deployment"]["reasons"])
    (tmp_path / "staging.json").write_text(json.dumps({"commit": "c1", "deploy": "PASS", "smoke": "PASS",
                                                       "backup_restore": "PASS", "rollback": "PASS"}))
    verdict = gate.decide(register, INVENTORY, _all_pass_results(), findings, commit="c1", dirty=[], suite_exit=0)
    assert verdict["decision"] == "GO_FOR_SUPERVISED_PREPRODUCTION", verdict["gates"]


def test_gate_has_no_bypass_switch():
    source = (ROOT / "scripts" / "preproduction_gate.py").read_text(encoding="utf-8")
    for flag in ("--force", "--bypass", "--override", "--skip-gate"):
        assert flag not in source


def test_junit_parsing_and_matching(tmp_path):
    junit = tmp_path / "j.xml"
    junit.write_text("""<testsuites><testsuite>
      <testcase classname="tests.agents.test_agent_engine" name="test_a"/>
      <testcase classname="tests.agents.test_agent_engine" name="test_b[x]"><skipped/></testcase>
      <testcase classname="tests.security.test_p0_boundaries" name="test_c"><failure/></testcase>
    </testsuite></testsuites>""")
    results = gate.parse_junit(junit)
    assert results == {"tests/agents/test_agent_engine.py::test_a": "passed",
                       "tests/agents/test_agent_engine.py::test_b[x]": "skipped",
                       "tests/security/test_p0_boundaries.py::test_c": "failed"}
    assert set(gate.matching(results, "tests/agents/test_agent_engine.py::test_b")) == {
        "tests/agents/test_agent_engine.py::test_b[x]"}
    assert len(gate.matching(results, "tests/agents/")) == 2
