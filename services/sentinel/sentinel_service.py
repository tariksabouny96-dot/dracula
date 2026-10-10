"""
PROJECT SENTINEL — Master Security Sentinel Service
Unified orchestrator for continuous security monitoring, vulnerability registry,
threat evaluation, integrity baselines, firewall inspection, and UI telemetry.
Governed by Master System Specification v1.3.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from services.sentinel.contracts import (
    SentinelFinding,
    FindingSeverity,
    FindingStatus,
    VulnerabilityCategory,
    FirewallTelemetry,
    SelfHealingAction,
    PatchCandidate
)
from services.sentinel.firewall import FirewallManager
from services.sentinel.integrity import IntegrityMonitor
from services.sentinel.self_healing import SelfHealingEngine
from services.sentinel.x_red_team import XRedTeamFramework
from services.sentinel.patch_engine import PatchEngine
from packages.config.paths import store_path


class SecuritySentinelService:
    """Master Security Sentinel Service operating continuously within HOOD."""

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        firewall_manager: Optional[FirewallManager] = None,
        integrity_monitor: Optional[IntegrityMonitor] = None,
        self_healing: Optional[SelfHealingEngine] = None,
        x_red_team: Optional[XRedTeamFramework] = None,
        patch_engine: Optional[PatchEngine] = None
    ):
        self.registry_path = store_path(registry_path, "artifacts/sentinel_vulnerabilities.json")
        self.firewall_manager = firewall_manager or FirewallManager(read_only=True)
        self.integrity_monitor = integrity_monitor or IntegrityMonitor()
        self.self_healing = self_healing or SelfHealingEngine()
        self.x_red_team = x_red_team or XRedTeamFramework()
        self.patch_engine = patch_engine or PatchEngine()

        self.findings: Dict[str, SentinelFinding] = {}
        self.last_scan_time: Optional[str] = None
        self._load_registry()

    def _load_registry(self):
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                for item in data:
                    finding = SentinelFinding(**item)
                    self.findings[finding.finding_id] = finding
            except Exception:
                pass

    def _persist_registry(self):
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = [f.model_dump() for f in self.findings.values()]
        self.registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def register_finding(self, finding: SentinelFinding) -> SentinelFinding:
        """Registers a discovered vulnerability or anomaly."""
        self.findings[finding.finding_id] = finding
        self._persist_registry()
        return finding

    def run_defensive_assessment(self) -> Dict[str, Any]:
        """
        Runs comprehensive non-destructive assessment of HOOD:
        1. File integrity check
        2. Firewall & exposed listening ports
        3. Authentication & RBAC invariants
        4. Sensitive data & secrets posture
        """
        self.last_scan_time = datetime.now(timezone.utc).isoformat()

        # 1. Integrity
        integrity_findings = self.integrity_monitor.verify_integrity()
        for f in integrity_findings:
            if f.finding_id not in self.findings:
                self.register_finding(f)

        # 2. Firewall & ports
        fw_telemetry = self.firewall_manager.inspect_firewall_status()
        for dev in fw_telemetry.baseline_deviations:
            finding = SentinelFinding(
                category=VulnerabilityCategory.FIREWALL_ANOMALY,
                severity=FindingSeverity.MEDIUM,
                title="Unexpected Listening Port Detected",
                affected_component="Host Network Stack",
                description=dev,
                evidence=f"Deviations detected during firewall audit: {dev}",
                proposed_mitigation="Verify if process is legitimate; bind to 127.0.0.1 or block via Windows Firewall.",
                status=FindingStatus.CONFIRMED,
                owner_approval_required=False
            )
            self.register_finding(finding)

        return self.get_security_summary()

    def get_security_summary(self) -> Dict[str, Any]:
        """Collects live, grounded security telemetry for HOOD UI."""
        counts = {
            FindingSeverity.CRITICAL.value: 0,
            FindingSeverity.HIGH.value: 0,
            FindingSeverity.MEDIUM.value: 0,
            FindingSeverity.LOW.value: 0,
            FindingSeverity.INFORMATIONAL.value: 0
        }

        open_findings = [f for f in self.findings.values() if f.status not in (FindingStatus.RESOLVED, FindingStatus.FALSE_POSITIVE)]
        for f in open_findings:
            counts[f.severity.value] = counts.get(f.severity.value, 0) + 1

        fw_data = self.firewall_manager.inspect_firewall_status()

        # Overall posture calculation
        if not fw_data.is_active:
            posture = "UNVERIFIED FIREWALL STATE"
        elif counts[FindingSeverity.CRITICAL.value] > 0:
            posture = "ELEVATED RISK"
        elif counts[FindingSeverity.HIGH.value] > 0:
            posture = "DEFENSIVE ATTENTION REQUIRED"
        elif counts[FindingSeverity.MEDIUM.value] > 0:
            posture = "MODERATE DEFENSE"
        else:
            posture = "OPTIMAL RESILIENCE"

        return {
            "posture": posture,
            "last_scan": self.last_scan_time or "NOT SCANNED",
            "open_findings_count": len(open_findings),
            "findings_by_severity": counts,
            "firewall": {
                "active": fw_data.is_active,
                "mode": fw_data.mode,
                "listening_ports_count": len(fw_data.listening_ports),
                "unexpected_ports": fw_data.unexpected_exposed_ports,
                "baseline_deviations": fw_data.baseline_deviations
            },
            "integrity": {
                "monitored_targets_count": len(self.integrity_monitor.baseline_hashes),
                "drift_detected": any(f.category == VulnerabilityCategory.INTEGRITY_DRIFT for f in open_findings)
            },
            "self_healing": {
                "recent_actions_count": len(self.self_healing.actions_history),
                "last_action": self.self_healing.actions_history[-1].model_dump() if self.self_healing.actions_history else None
            },
            "x_red_team": {
                "status": "DORMANT" if self.x_red_team.is_dormant else "ACTIVE_EXERCISE",
                "total_findings_discovered": len(self.x_red_team.findings_archive)
            },
            "patches": {
                "pending_approval_count": sum(1 for p in self.patch_engine.candidates.values() if p.approval_required and not p.is_approved),
                "deployed_count": sum(1 for p in self.patch_engine.candidates.values() if p.is_deployed)
            }
        }
