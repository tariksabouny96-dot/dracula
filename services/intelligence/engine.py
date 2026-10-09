"""
HOOD Continuous Technology Intelligence Engine
Governed by Master System Specification v1.2 Section 4.

Discovers, verifies, compares, and benchmark-tests emerging advances across:
- AI / ML / Model architectures / Inference optimization
- Training methods / Quantization / LoRA / Distillation
- Programming languages / Compilers / Rust / Mojo / Python
- Operating systems / Hardware / Multi-GPU topology / Cloud
- Cybersecurity / AppSec / Sandbox isolation
- Scientific / Automation / Business opportunities

Pipeline:
DISCOVER -> VERIFY -> COMPARE -> EXPERIMENT -> BENCHMARK -> RECOMMEND / ADOPT.
"""

from __future__ import annotations
import json
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

from services.impossible_list.service import ImpossibleListService


class TechCategory(str, Enum):
    AI_ML_MODELS = "AI_ML_MODELS"
    INFERENCE_OPTIMIZATION = "INFERENCE_OPTIMIZATION"
    TRAINING_METHODS = "TRAINING_METHODS"
    LANGUAGES_RUNTIMES = "LANGUAGES_RUNTIMES"
    SYSTEMS_DISTRIBUTED = "SYSTEMS_DISTRIBUTED"
    HARDWARE_COMPUTE = "HARDWARE_COMPUTE"
    CYBERSECURITY = "CYBERSECURITY"
    AUTOMATION_ROBOTICS = "AUTOMATION_ROBOTICS"
    BUSINESS_TECH = "BUSINESS_TECH"


class DiscoveryAssessment(BaseModel):
    improves_capability: bool = False
    reduces_cost: bool = False
    reduces_dependency: bool = False
    improves_reliability: bool = False
    improves_security: bool = False
    reduces_hardware_requirements: bool = False
    enables_impossible_tasks: List[str] = Field(default_factory=list)
    overall_recommendation: str = "MONITOR"  # ADOPT, EXPERIMENT, MONITOR, REJECT
    evidence: str = ""


class TechDiscoveryItem(BaseModel):
    tech_id: str
    title: str
    category: TechCategory
    source_url_or_ref: str
    summary: str
    license_type: str
    hardware_requirements: str
    assessment: DiscoveryAssessment
    date_discovered: str
    is_sandboxed_verified: bool = False
    verification_notes: Optional[str] = None


class TechnologyIntelligenceEngine:
    """Discovers, tracks, evaluates, and alerts on frontier tech opportunities."""

    def __init__(
        self,
        db_path: Optional[Path] = None,
        impossible_list: Optional[ImpossibleListService] = None
    ):
        self.db_path = db_path or Path("artifacts/tech_intelligence.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.impossible_list = impossible_list or ImpossibleListService()
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS tech_discoveries (
                tech_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                source_url_or_ref TEXT NOT NULL,
                summary TEXT NOT NULL,
                license_type TEXT NOT NULL,
                hardware_requirements TEXT NOT NULL,
                assessment TEXT NOT NULL,
                date_discovered TEXT NOT NULL,
                is_sandboxed_verified INTEGER NOT NULL DEFAULT 0,
                verification_notes TEXT
            );
            """)

    def register_discovery(
        self,
        tech_id: str,
        title: str,
        category: TechCategory,
        source_url_or_ref: str,
        summary: str,
        license_type: str,
        hardware_requirements: str,
        assessment: DiscoveryAssessment,
        is_sandboxed_verified: bool = False,
        verification_notes: Optional[str] = None
    ) -> TechDiscoveryItem:
        """Records a new technology intelligence discovery and checks against the Impossible List."""
        # Cross-reference with impossible list
        unlocked = self.impossible_list.evaluate_unlocked_by_technology(title)
        if unlocked:
            assessment.enables_impossible_tasks = [u.item_id for u in unlocked]
            if assessment.overall_recommendation == "MONITOR":
                assessment.overall_recommendation = "EXPERIMENT"

        now = datetime.now(timezone.utc).isoformat()
        item = TechDiscoveryItem(
            tech_id=tech_id,
            title=title,
            category=category,
            source_url_or_ref=source_url_or_ref,
            summary=summary,
            license_type=license_type,
            hardware_requirements=hardware_requirements,
            assessment=assessment,
            date_discovered=now,
            is_sandboxed_verified=is_sandboxed_verified,
            verification_notes=verification_notes
        )

        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO tech_discoveries (
                tech_id, title, category, source_url_or_ref, summary,
                license_type, hardware_requirements, assessment,
                date_discovered, is_sandboxed_verified, verification_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.tech_id,
                item.title,
                item.category.value,
                item.source_url_or_ref,
                item.summary,
                item.license_type,
                item.hardware_requirements,
                item.assessment.model_dump_json(),
                item.date_discovered,
                1 if item.is_sandboxed_verified else 0,
                item.verification_notes
            ))
        return item

    def get_discovery(self, tech_id: str) -> Optional[TechDiscoveryItem]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tech_discoveries WHERE tech_id = ?", (tech_id,)).fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    def list_recommendations(self, recommendation: Optional[str] = None) -> List[TechDiscoveryItem]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM tech_discoveries ORDER BY date_discovered DESC").fetchall()
            items = [self._row_to_item(r) for r in rows]
            if recommendation:
                return [i for i in items if i.assessment.overall_recommendation == recommendation]
            return items

    def _row_to_item(self, row: sqlite3.Row) -> TechDiscoveryItem:
        return TechDiscoveryItem(
            tech_id=row["tech_id"],
            title=row["title"],
            category=TechCategory(row["category"]),
            source_url_or_ref=row["source_url_or_ref"],
            summary=row["summary"],
            license_type=row["license_type"],
            hardware_requirements=row["hardware_requirements"],
            assessment=DiscoveryAssessment.model_validate_json(row["assessment"]),
            date_discovered=row["date_discovered"],
            is_sandboxed_verified=bool(row["is_sandboxed_verified"]),
            verification_notes=row["verification_notes"]
        )
