"""
Tests for PROJECT SENTINEL — Unified Cybersecurity, Firewall, Self-Healing,
Patch Engine, Integrity, and X Red-Team Governance.
Governed by Master System Specification v1.3.
"""

import pytest
import json
from pathlib import Path

from services.sentinel import (
    SentinelFinding,
    FindingSeverity,
    FindingStatus,
    VulnerabilityCategory,
    FirewallManager,
    IntegrityMonitor,
    SelfHealingEngine,
    XRedTeamFramework,
    IndependentValidationRunner,
    PatchEngine,
    SecuritySentinelService
)


@pytest.fixture
def temp_sentinel_env(tmp_path):
    registry_file = tmp_path / "sentinel_test_vulns.json"
    baseline_file = tmp_path / "sentinel_test_baseline.json"
    patches_dir = tmp_path / "sentinel_test_patches"
    actions_log = tmp_path / "sentinel_test_healing.log"
    return {
        "registry_file": registry_file,
        "baseline_file": baseline_file,
        "patches_dir": patches_dir,
        "actions_log": actions_log
    }


def test_firewall_inspection_and_proposal():
    fw = FirewallManager(read_only=True)
    telemetry = fw.inspect_firewall_status()
    if __import__("sys").platform != "win32":
        assert telemetry.is_active is False
    else:
        assert isinstance(telemetry.is_active, bool)
    assert telemetry.mode == "READ-ONLY"
    assert len(telemetry.profiles) >= 1
    assert isinstance(telemetry.listening_ports, list)

    # Propose rule change requires approval and provides rollback
    proposal = fw.propose_rule_change(action="block", port=4444, direction="in")
    assert proposal["status"] == "APPROVAL_REQUIRED"
    assert "netsh advfirewall firewall add rule" in proposal["proposed_command"]
    assert "netsh advfirewall firewall delete rule" in proposal["rollback_command"]


def test_integrity_monitoring_and_drift_detection(temp_sentinel_env, tmp_path):
    target = tmp_path / "dummy_critical_file.py"
    target.write_text("print('Secure Baseline v1.0')", encoding="utf-8")

    monitor = IntegrityMonitor(baseline_file=temp_sentinel_env["baseline_file"])
    # Point target to monitored list
    monitor.CRITICAL_TARGETS = [target]
    monitor.generate_baseline()

    # Invariant verified
    findings = monitor.verify_integrity()
    assert len(findings) == 0

    # Modify file -> Drift detected
    target.write_text("print('Unauthorized Tampering')", encoding="utf-8")
    drift_findings = monitor.verify_integrity()
    assert len(drift_findings) == 1
    assert drift_findings[0].category == VulnerabilityCategory.INTEGRITY_DRIFT
    assert drift_findings[0].severity == FindingSeverity.HIGH


def test_self_healing_delegated_repairs_and_immutable_boundaries(temp_sentinel_env, tmp_path):
    engine = SelfHealingEngine(actions_log=temp_sentinel_env["actions_log"])

    # Low risk cache repair
    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir()
    (cache_dir / "temp_file.txt").write_text("corrupted", encoding="utf-8")

    action = engine.repair_disposable_cache(cache_dir)
    assert action.success is False
    assert action.executed is False
    assert (cache_dir / "temp_file.txt").exists()
    assert action.risk_level == "L1"

    # Protected boundary repair attempt is BLOCKED autonomously
    blocked_action = engine.attempt_governed_repair("ROOT_OWNER_IDENTITY", "Modify master owner identity")
    assert blocked_action.is_authorized is False
    assert blocked_action.executed is False
    assert "BLOCKED by Sentinel Policy" in blocked_action.details


def test_x_red_team_dormancy_and_authorized_exercise():
    x = XRedTeamFramework()
    assert x.is_dormant is True

    # Active probe while dormant raises PermissionError
    with pytest.raises(PermissionError, match="X is DORMANT"):
        x.test_path_traversal(lambda p: "data")

    # Unauthorized activation fails
    with pytest.raises(PermissionError, match="Only ZACK"):
        x.activate_exercise(authorized_by="intruder", target_name="SANDBOX", categories=[])

    # Authorized activation by ZACK succeeds
    scope = x.activate_exercise(authorized_by="zack", target_name="ISOLATED_SANDBOX", categories=[])
    assert x.is_dormant is False
    assert scope.is_authorized_by_zack is True

    # Fuzzing probe finds path traversal
    vulnerable_reader = lambda path: "SECRET_PASSWORD_FILE" if "passwd" in path else None
    finding = x.test_path_traversal(vulnerable_reader)
    assert finding is not None
    assert finding.category == VulnerabilityCategory.PATH_TRAVERSAL
    assert finding.discovered_by == "X_RED_TEAM"

    # Stand down returns X to dormant
    stand_down_res = x.stand_down()
    assert stand_down_res["status"] == "X_DORMANT"
    assert x.is_dormant is True


def test_independent_patch_verification_and_deployment(temp_sentinel_env, tmp_path):
    patch_engine = PatchEngine(patches_dir=temp_sentinel_env["patches_dir"])

    dummy_finding = SentinelFinding(
        category=VulnerabilityCategory.PATH_TRAVERSAL,
        severity=FindingSeverity.HIGH,
        title="Path Traversal Flaw",
        affected_component="file_reader.py",
        description="Reads arbitrary paths",
        evidence="Path traversal confirmed"
    )

    orig_code = "def read(path): return open(path).read()"
    patched_code = "def read(path): p = Path(path).resolve(); return p.read_text()"

    candidate = patch_engine.generate_candidate_patch(
        finding=dummy_finding,
        original_code=orig_code,
        remediated_code=patched_code,
        root_cause="Missing path confinement",
        remediation_summary="Resolved path strictly within safe root",
        regression_tests=["test_read_confined"]
    )

    assert candidate.layer1_developer_passed is True

    # Layer 3 Independent Validation
    validation = patch_engine.verify_candidate_patch(
        patch_id=candidate.patch_id,
        unpatched_probe_result=True,  # Flaw was reproducible on unpatched baseline
        patched_probe_result=True,    # Flaw was prevented on patched build
        regression_suite_result=True  # Regression tests all pass
    )
    assert validation["verified"] is True
    assert candidate.layer3_independent_validator_passed is True

    # Owner approval required before deployment
    target_file = tmp_path / "file_reader.py"
    target_file.write_text(orig_code, encoding="utf-8")

    with pytest.raises(PermissionError, match="has not received Root Owner approval"):
        patch_engine.deploy_patch(candidate.patch_id, target_file, patched_code)

    # Approve and deploy
    patch_engine.record_owner_approval(candidate.patch_id, approved_by="zack")
    deploy_res = patch_engine.deploy_patch(candidate.patch_id, target_file, patched_code)
    assert deploy_res["status"] == "DEPLOYED"
    assert target_file.read_text(encoding="utf-8") == patched_code


def test_master_sentinel_service_assessment_and_telemetry(temp_sentinel_env):
    service = SecuritySentinelService(registry_path=temp_sentinel_env["registry_file"])
    summary = service.run_defensive_assessment()

    assert "posture" in summary
    assert "firewall" in summary
    assert isinstance(summary["firewall"]["active"], bool)
    if not summary["firewall"]["active"]:
        assert summary["posture"] == "UNVERIFIED FIREWALL STATE"
    assert summary["firewall"]["mode"] == "READ-ONLY"
    assert summary["x_red_team"]["status"] == "DORMANT"
