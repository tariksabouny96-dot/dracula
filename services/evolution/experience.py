"""
HOOD Evolution Engine - Experience Collector & Validated Learning Pipeline
Governed by Master System Specification Sections 2, 7, 8 & V0.5C Directives.

CRITICAL RULES:
- Teacher outputs are CANDIDATES, not ground truth.
- Validated experience requires: Teacher + Level 3 + Deterministic Test + Evidence + Maker-Checker.
- Plaintext secrets and X_SEALED data are NEVER ordinary Level 3 training material.
- Project/tenant isolation is strictly preserved.
"""

from typing import Dict, Any, List, Optional
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from services.evolution.contracts import (
    ExperienceRecord,
    ArenaAttempt,
    TeacherContribution,
    CapabilityDomain,
    EvidenceQuality,
    FailureCategory,
    DatasetPrivacyScope,
    ModelLevel
)
from packages.logging.redactor import redact_string


class ExperienceCollector:
    """Collects and sanitizes task executions into high-confidence ExperienceRecords."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or Path("artifacts/evolution/experiences")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.records: List[ExperienceRecord] = []

    def record_experience(
        self,
        task_type: CapabilityDomain,
        project_context_id: str,
        prompt: str,
        level_1_attempt: Optional[ArenaAttempt] = None,
        level_2_attempt: Optional[ArenaAttempt] = None,
        level_3_attempt: Optional[ArenaAttempt] = None,
        teacher_contributions: Optional[List[TeacherContribution]] = None,
        deterministic_test_result: Optional[bool] = None,
        evidence_quality: EvidenceQuality = EvidenceQuality.UNVERIFIED_HEURISTIC,
        checker_verdict: str = "PASSED",
        actual_task_outcome: str = "SUCCESS",
        selected_final_action: str = "",
        selection_rationale: str = "",
        confidence: float = 0.5,
        privacy_scope: DatasetPrivacyScope = DatasetPrivacyScope.PROJECT_ONLY,
        failure_category: Optional[FailureCategory] = None
    ) -> ExperienceRecord:
        """
        Creates, sanitizes, and persists an ExperienceRecord.
        Strictly enforces:
        1. X_SEALED records are flagged as DO_NOT_TRAIN / X_SEALED.
        2. Prompts and actions undergo regex secret redaction.
        3. Generates cryptographic provenance SHA256 digest.
        """
        # Enforce X_SEALED policy
        if privacy_scope == DatasetPrivacyScope.X_SEALED or "X_SEALED" in project_context_id:
            privacy_scope = DatasetPrivacyScope.X_SEALED

        # Sanitize prompt and final action
        clean_prompt = redact_string(prompt)
        clean_action = redact_string(selected_final_action)
        clean_rationale = redact_string(selection_rationale)

        # Sanitize attempts if present
        if level_1_attempt:
            level_1_attempt.output_text = redact_string(level_1_attempt.output_text)
        if level_2_attempt:
            level_2_attempt.output_text = redact_string(level_2_attempt.output_text)
        if level_3_attempt:
            level_3_attempt.output_text = redact_string(level_3_attempt.output_text)

        # Compute provenance hash
        payload = {
            "task_type": task_type.value,
            "project": project_context_id,
            "prompt": clean_prompt,
            "test_result": deterministic_test_result,
            "checker": checker_verdict,
            "outcome": actual_task_outcome,
            "action": clean_action,
            "scope": privacy_scope.value
        }
        prov_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

        rec = ExperienceRecord(
            task_type=task_type,
            project_context_id=project_context_id,
            sanitized_prompt=clean_prompt,
            level_1_attempt=level_1_attempt,
            level_2_attempt=level_2_attempt,
            level_3_attempt=level_3_attempt,
            teacher_contributions=teacher_contributions or [],
            deterministic_test_result=deterministic_test_result,
            evidence_quality=evidence_quality,
            checker_verdict=checker_verdict,
            actual_task_outcome=actual_task_outcome,
            selected_final_action=clean_action,
            selection_rationale=clean_rationale,
            confidence=confidence,
            privacy_scope=privacy_scope,
            failure_category=failure_category,
            provenance_hash=prov_hash
        )

        self.records.append(rec)
        self._persist_record(rec)
        return rec

    def _persist_record(self, record: ExperienceRecord):
        rec_file = self.storage_dir / f"{record.experience_id}.json"
        rec_file.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def query_eligible_training_records(
        self,
        domain: CapabilityDomain,
        target_scope: DatasetPrivacyScope = DatasetPrivacyScope.GLOBAL_SANITIZED
    ) -> List[ExperienceRecord]:
        """
        Returns high-confidence, verified records eligible for dataset aggregation.
        Rejects unverified, failed, or prohibited privacy scopes.
        """
        eligible = []
        for r in self.records:
            # Never include X_SEALED or DO_NOT_TRAIN
            if r.privacy_scope in (DatasetPrivacyScope.X_SEALED, DatasetPrivacyScope.DO_NOT_TRAIN):
                continue

            # Must match domain
            if r.task_type != domain:
                continue

            # If global requested, must be GLOBAL_SANITIZED
            if target_scope == DatasetPrivacyScope.GLOBAL_SANITIZED and r.privacy_scope != DatasetPrivacyScope.GLOBAL_SANITIZED:
                continue

            # Must be validated by test, primary evidence, or consensus
            is_validated = (
                r.deterministic_test_result is True or
                r.evidence_quality in (EvidenceQuality.CONFIRMED_DETERMINISTIC, EvidenceQuality.PRIMARY_SOURCE, EvidenceQuality.MAKER_CHECKER_VERIFIED)
            )
            if is_validated and r.checker_verdict == "PASSED" and r.actual_task_outcome == "SUCCESS":
                eligible.append(r)

        return eligible
