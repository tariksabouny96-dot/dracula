"""
HOOD Evolution Engine - Dataset Builder & Privacy Isolator
Governed by Master System Specification Sections 2, 7 & V0.5C Directives.

CRITICAL RULES:
- Sanitizes and exports versioned training manifests.
- STRICT GUARANTEES:
  1. Plaintext secrets are stripped.
  2. X_SEALED records are NEVER included in ordinary Level 3 datasets.
  3. PROJECT_ONLY data is never automatically promoted to GLOBAL_SANITIZED.
  4. DO_NOT_TRAIN records are strictly excluded.
"""

from typing import Dict, Any, List, Optional
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from services.evolution.contracts import (
    ExperienceRecord,
    TrainingDatasetManifest,
    CapabilityDomain,
    DatasetPrivacyScope
)
from services.evolution.experience import ExperienceCollector


class DatasetBuilder:
    """Builds cryptographically verifiable, privacy-isolated training manifests."""

    def __init__(self, experience_collector: ExperienceCollector, output_dir: Optional[Path] = None):
        self.collector = experience_collector
        self.output_dir = output_dir or Path("artifacts/evolution/datasets")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_dataset(
        self,
        domain: CapabilityDomain,
        version: str,
        target_scope: DatasetPrivacyScope = DatasetPrivacyScope.GLOBAL_SANITIZED
    ) -> TrainingDatasetManifest:
        """
        Aggregates verified experience records into a versioned training manifest.
        Enforces strict exclusion of secrets, X_SEALED records, and project leakage.
        """
        # Enforce that ordinary Level 3 datasets never target X_SEALED
        if target_scope == DatasetPrivacyScope.X_SEALED:
            raise PermissionError("X_SEALED cannot be targeted for ordinary Level 3 dataset builds.")

        eligible_records = self.collector.query_eligible_training_records(
            domain=domain,
            target_scope=target_scope
        )

        record_payloads = []
        for r in eligible_records:
            # Extra safety check against X_SEALED or DO_NOT_TRAIN
            if r.privacy_scope in (DatasetPrivacyScope.X_SEALED, DatasetPrivacyScope.DO_NOT_TRAIN):
                continue
            record_payloads.append({
                "experience_id": r.experience_id,
                "prompt": r.sanitized_prompt,
                "action": r.selected_final_action,
                "confidence": r.confidence,
                "provenance_hash": r.provenance_hash
            })

        # Deterministic JSON canonical hash
        canonical = json.dumps(record_payloads, sort_keys=True)
        manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        dataset_id = f"ds_{domain.value.lower()}_{version}"
        manifest = TrainingDatasetManifest(
            dataset_id=dataset_id,
            version=version,
            domain=domain,
            record_count=len(record_payloads),
            allowed_privacy_scope=target_scope,
            records_hash=manifest_hash,
            secrets_sanitized=True,
            x_sealed_strictly_excluded=True
        )

        # Write manifest and data file
        dataset_file = self.output_dir / f"{dataset_id}.json"
        dataset_payload = {
            "manifest": manifest.model_dump(),
            "records": record_payloads
        }
        dataset_file.write_text(json.dumps(dataset_payload, indent=2), encoding="utf-8")

        return manifest
