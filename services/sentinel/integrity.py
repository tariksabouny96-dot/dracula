"""
PROJECT SENTINEL — Critical File & Runtime Integrity Monitor
Monitors critical codebase files and security policies against unauthorized modifications.
Generates cryptographic baseline hashes and detects integrity drift.
Governed by Master System Specification v1.3 Section 15.
"""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

from services.sentinel.contracts import SentinelFinding, VulnerabilityCategory, FindingSeverity, FindingStatus


class IntegrityMonitor:
    """Computes SHA-256 baselines of critical security assets and detects unauthorized modifications."""

    CRITICAL_TARGETS = [
        Path("services/auth/auth_service.py"),
        Path("services/core/emergency_stop.py"),
        Path("services/x_control/x_executive.py"),
        Path("packages/auth/vault.py"),
        Path("ui/server.py"),
    ]

    def __init__(self, baseline_file: Optional[Path] = None):
        self.baseline_file = baseline_file or Path("artifacts/sentinel_integrity_baseline.json")
        self.baseline_hashes: Dict[str, str] = {}
        self._load_or_create_baseline()

    def _hash_file(self, path: Path) -> Optional[str]:
        if not path.exists():
            return None
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _load_or_create_baseline(self):
        if self.baseline_file.exists():
            try:
                self.baseline_hashes = json.loads(self.baseline_file.read_text(encoding="utf-8"))
                return
            except Exception:
                pass

        # Generate initial baseline
        self.generate_baseline()

    def generate_baseline(self) -> Dict[str, str]:
        """Snapshots current verified state of critical security files."""
        new_baseline = {}
        for target in self.CRITICAL_TARGETS:
            if target.exists():
                h = self._hash_file(target)
                if h:
                    new_baseline[str(target).replace("\\", "/")] = h

        self.baseline_hashes = new_baseline
        self.baseline_file.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_file.write_text(json.dumps(new_baseline, indent=2), encoding="utf-8")
        return new_baseline

    def verify_integrity(self) -> List[SentinelFinding]:
        """Checks current disk hashes against baseline and flags drift."""
        findings: List[SentinelFinding] = []
        for target_str, expected_hash in self.baseline_hashes.items():
            path = Path(target_str)
            if not path.exists():
                findings.append(SentinelFinding(
                    category=VulnerabilityCategory.INTEGRITY_DRIFT,
                    severity=FindingSeverity.CRITICAL,
                    title="Critical Security File Missing",
                    affected_component=target_str,
                    description=f"Critical system security file {target_str} has been deleted or moved.",
                    evidence=f"Expected SHA-256: {expected_hash}, file not found on disk.",
                    status=FindingStatus.CONFIRMED,
                    owner_approval_required=True
                ))
                continue

            current_hash = self._hash_file(path)
            if current_hash != expected_hash:
                findings.append(SentinelFinding(
                    category=VulnerabilityCategory.INTEGRITY_DRIFT,
                    severity=FindingSeverity.HIGH,
                    title="Critical File Integrity Drift Detected",
                    affected_component=target_str,
                    description=f"Cryptographic hash mismatch detected on critical security component {target_str}.",
                    evidence=f"Expected: {expected_hash[:16]}... Current: {current_hash[:16]}...",
                    proposed_mitigation="Verify if change was authorized by ZACK; restore from git checkpoint if unauthorized.",
                    status=FindingStatus.CONFIRMED,
                    owner_approval_required=True
                ))

        return findings
