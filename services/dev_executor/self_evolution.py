"""
HOOD Autonomous Self-Evolution Engine
Governed by Master System Specification v1.2 Section 3.

Implements the 18-step autonomous self-development loop:
1. Continuous improvement detection
2. Automatic bug discovery
3. Root-cause analysis
4. Technical research
5. Candidate solution generation
6. Independent solution comparison
7. Sandboxed implementation
8. Automated testing
9. Performance benchmarking
10. Security assessment
11. Compatibility testing
12. Regression detection
13. Automatic documentation
14. Versioned checkpoints
15. Safe deployment
16. Runtime monitoring
17. Automatic rollback on failure
18. Learning from successful and unsuccessful changes

Authority Levels:
- LOW RISK: Autonomously implement verified, reversible changes in authorized local boundary; notify Zak afterward.
- MEDIUM RISK: Research, prototype, test; deploy only where policy explicitly authorizes.
- HIGH / CRITICAL RISK: Prepare complete upgrade package; STOP -> EXPLAIN -> RECOMMEND -> REQUIRE ZAK APPROVAL.
"""

from __future__ import annotations
import json
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from packages.contracts import RiskLevel
from services.evolution.contracts import CapabilityDomain
from services.dev_executor.service import DevelopmentExecutor
from services.policy.approval_service import ApprovalService
from services.evolution.acceleration import Level3AccelerationEngine
from packages.config.paths import store_path


class SelfDevRiskCategory(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SelfDevChangeNotification(BaseModel):
    change_id: str
    what_changed: str
    why_it_changed: str
    expected_benefit: str
    tests_performed: List[str] = Field(default_factory=list)
    actual_result: str
    risk_level: SelfDevRiskCategory
    requires_approval: bool
    is_approved: bool = False
    rollback_status: str
    checkpoint_ref: Optional[str] = None
    timestamp: str


class SelfEvolutionEngine:
    """Orchestrates autonomous inspection, bug discovery, test-driven patching, and governed rollback."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        dev_executor: Optional[DevelopmentExecutor] = None,
        approval_service: Optional[ApprovalService] = None,
        acceleration_engine: Optional[Level3AccelerationEngine] = None,
        db_path: Optional[Path] = None
    ):
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.dev_executor = dev_executor or DevelopmentExecutor(self.workspace_root)
        self.approval_service = approval_service or ApprovalService()
        self.acceleration_engine = acceleration_engine or Level3AccelerationEngine(store_path(None, "artifacts/acceleration_engine.db"))
        self.db_path = store_path(db_path, "artifacts/self_evolution.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS self_dev_changes (
                change_id TEXT PRIMARY KEY,
                what_changed TEXT NOT NULL,
                why_it_changed TEXT NOT NULL,
                expected_benefit TEXT NOT NULL,
                tests_performed TEXT NOT NULL,
                actual_result TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                requires_approval INTEGER NOT NULL,
                is_approved INTEGER NOT NULL,
                rollback_status TEXT NOT NULL,
                checkpoint_ref TEXT,
                timestamp TEXT NOT NULL
            );
            """)

    def execute_self_improvement_cycle(
        self,
        change_id: str,
        target_file_rel: str,
        proposed_patch_chunk: str,
        original_chunk: str,
        why: str,
        benefit: str,
        test_path: str = "tests"
    ) -> SelfDevChangeNotification:
        """
        Executes an end-to-end governed self-evolution change:
        1. Classifies risk
        2. Checkpoints file
        3. Applies patch
        4. Runs regression test suite
        5. If tests fail -> automatic atomic rollback
        6. If tests pass -> confirms change and records notification
        """
        # Risk classification
        is_constitutional = any(k in target_file_rel.lower() for k in ["constitution", "policy", "vault", "x_control", "emergency_stop"])
        if is_constitutional:
            risk = SelfDevRiskCategory.CRITICAL
            requires_approval = True
        else:
            risk = SelfDevRiskCategory.LOW
            requires_approval = False

        if requires_approval:
            raise PermissionError(
                "Critical self-evolution is disabled until a real, actor-bound approval "
                "can be verified BEFORE patch application. No files were modified."
            )

        # Apply reversible modification
        edit_res = self.dev_executor.apply_reversible_code_change(
            target_file=target_file_rel,
            content=proposed_patch_chunk,
            is_patch_replace=True,
            original_chunk=original_chunk,
            replacement_chunk=proposed_patch_chunk
        )

        tests_run = [test_path]
        test_report = self.dev_executor.run_tests(test_path=test_path)

        if not test_report.success:
            # Step 17: Automatic rollback on failure
            self.dev_executor.rollback_code_change(target_file_rel, edit_res.checkpoint_ref)
            rollback_status = "ROLLED_BACK_DUE_TO_TEST_FAILURE"
            actual_res = f"Tests failed ({test_report.total_failed} failures). Changes safely reverted."
            
            # Step 18: Record lesson from unsuccessful change
            self.acceleration_engine.extract_lesson_from_failure(
                lesson_id=f"failure_{change_id}",
                domain=CapabilityDomain.CODING,
                source_failure=f"Self-improvement on {target_file_rel} broke tests",
                root_cause=f"Regression in {test_path}: exit code {test_report.exit_code}",
                corrective_rule="Preserve contract signatures and run unit test prior to promotion",
                regression_test=test_path
            )
        else:
            rollback_status = "STABLE_PRESERVED"
            actual_res = f"All {test_report.total_passed} tests passed successfully."

        now = datetime.now(timezone.utc).isoformat()
        notif = SelfDevChangeNotification(
            change_id=change_id,
            what_changed=f"Applied patch to {target_file_rel}",
            why_it_changed=why,
            expected_benefit=benefit,
            tests_performed=tests_run,
            actual_result=actual_res,
            risk_level=risk,
            requires_approval=requires_approval,
            is_approved=not requires_approval,
            rollback_status=rollback_status,
            checkpoint_ref=edit_res.checkpoint_ref,
            timestamp=now
        )

        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO self_dev_changes (
                change_id, what_changed, why_it_changed, expected_benefit,
                tests_performed, actual_result, risk_level, requires_approval,
                is_approved, rollback_status, checkpoint_ref, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                notif.change_id,
                notif.what_changed,
                notif.why_it_changed,
                notif.expected_benefit,
                json.dumps(notif.tests_performed),
                notif.actual_result,
                notif.risk_level.value,
                1 if notif.requires_approval else 0,
                1 if notif.is_approved else 0,
                notif.rollback_status,
                notif.checkpoint_ref,
                notif.timestamp
            ))

        return notif
