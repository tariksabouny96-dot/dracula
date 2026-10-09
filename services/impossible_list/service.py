"""
HOOD Impossible List Service - Persistent Frontier Barrier Tracking
Governed by Master System Specification v1.2 Section 5.

Distinguishes between:
1. NOT POSSIBLE WITH CURRENT RESOURCES
2. NOT TECHNICALLY POSSIBLE (Frontier / Science frontier)
3. NOT AUTHORIZED OR LEGALLY PERMITTED (Governance / Law)

Technological improvements never override constitutional boundaries.
"""

from __future__ import annotations
import json
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class BarrierCategory(str, Enum):
    RESOURCE_CONSTRAINED = "NOT_POSSIBLE_WITH_CURRENT_RESOURCES"
    TECHNICALLY_IMPOSSIBLE = "NOT_TECHNICALLY_POSSIBLE"
    UNAUTHORIZED_OR_ILLEGAL = "NOT_AUTHORIZED_OR_LEGALLY_PERMITTED"


class ImpossibleItem(BaseModel):
    item_id: str
    objective: str
    category: BarrierCategory
    limitation_summary: str
    evidence_supporting: str
    required_capability: str
    potential_technological_unlock: str
    estimated_economic_value_usd: float = 0.0
    resource_requirements: Dict[str, Any] = Field(default_factory=dict)
    date_recorded: str
    date_last_evaluated: str
    reevaluation_triggers: List[str] = Field(default_factory=list)
    is_resolved: bool = False
    resolution_notes: Optional[str] = None


class ImpossibleListService:
    """Manages persistent tracking, evaluation, and revisiting of blocked objectives."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path("artifacts/impossible_list.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS impossible_list (
                item_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                category TEXT NOT NULL,
                limitation_summary TEXT NOT NULL,
                evidence_supporting TEXT NOT NULL,
                required_capability TEXT NOT NULL,
                potential_technological_unlock TEXT NOT NULL,
                estimated_economic_value_usd REAL NOT NULL,
                resource_requirements TEXT NOT NULL,
                date_recorded TEXT NOT NULL,
                date_last_evaluated TEXT NOT NULL,
                reevaluation_triggers TEXT NOT NULL,
                is_resolved INTEGER NOT NULL DEFAULT 0,
                resolution_notes TEXT
            );
            """)

    def record_barrier(
        self,
        item_id: str,
        objective: str,
        category: BarrierCategory,
        limitation_summary: str,
        evidence_supporting: str,
        required_capability: str,
        potential_technological_unlock: str,
        estimated_value_usd: float = 0.0,
        resource_requirements: Optional[Dict[str, Any]] = None,
        reevaluation_triggers: Optional[List[str]] = None
    ) -> ImpossibleItem:
        """Records a new barrier into the persistent impossible list."""
        now = datetime.now(timezone.utc).isoformat()
        item = ImpossibleItem(
            item_id=item_id,
            objective=objective,
            category=category,
            limitation_summary=limitation_summary,
            evidence_supporting=evidence_supporting,
            required_capability=required_capability,
            potential_technological_unlock=potential_technological_unlock,
            estimated_economic_value_usd=estimated_value_usd,
            resource_requirements=resource_requirements or {},
            date_recorded=now,
            date_last_evaluated=now,
            reevaluation_triggers=reevaluation_triggers or [],
            is_resolved=False
        )

        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO impossible_list (
                item_id, objective, category, limitation_summary, evidence_supporting,
                required_capability, potential_technological_unlock, estimated_economic_value_usd,
                resource_requirements, date_recorded, date_last_evaluated,
                reevaluation_triggers, is_resolved, resolution_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.item_id,
                item.objective,
                item.category.value,
                item.limitation_summary,
                item.evidence_supporting,
                item.required_capability,
                item.potential_technological_unlock,
                item.estimated_economic_value_usd,
                json.dumps(item.resource_requirements),
                item.date_recorded,
                item.date_last_evaluated,
                json.dumps(item.reevaluation_triggers),
                1 if item.is_resolved else 0,
                item.resolution_notes
            ))
        return item

    def get_barrier(self, item_id: str) -> Optional[ImpossibleItem]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM impossible_list WHERE item_id = ?", (item_id,)).fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    def list_unresolved(self, category: Optional[BarrierCategory] = None) -> List[ImpossibleItem]:
        with self._get_connection() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM impossible_list WHERE is_resolved = 0 AND category = ? ORDER BY estimated_economic_value_usd DESC",
                    (category.value,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM impossible_list WHERE is_resolved = 0 ORDER BY estimated_economic_value_usd DESC"
                ).fetchall()
            return [self._row_to_item(r) for r in rows]

    def evaluate_unlocked_by_technology(self, keyword: str) -> List[ImpossibleItem]:
        """Scans for barriers that could be revisited given a new technical breakthrough or hardware capability."""
        tokens = [t.lower() for t in keyword.split() if len(t) > 3]
        with self._get_connection() as conn:
            rows = conn.execute("""
            SELECT * FROM impossible_list
            WHERE is_resolved = 0
              AND category != 'NOT_AUTHORIZED_OR_LEGALLY_PERMITTED'
            """).fetchall()
            matching = []
            for r in rows:
                unlock = r["potential_technological_unlock"].lower()
                triggers = r["reevaluation_triggers"].lower()
                # Check if keyword is substring or any significant token matches
                if keyword.lower() in unlock or keyword.lower() in triggers:
                    matching.append(self._row_to_item(r))
                    continue
                if any(tok in unlock or tok in triggers for tok in tokens):
                    matching.append(self._row_to_item(r))
            return matching

    def mark_resolved(self, item_id: str, notes: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.execute("""
            UPDATE impossible_list
            SET is_resolved = 1, resolution_notes = ?, date_last_evaluated = ?
            WHERE item_id = ?
            """, (notes, now, item_id))
            return cursor.rowcount > 0

    def _row_to_item(self, row: sqlite3.Row) -> ImpossibleItem:
        return ImpossibleItem(
            item_id=row["item_id"],
            objective=row["objective"],
            category=BarrierCategory(row["category"]),
            limitation_summary=row["limitation_summary"],
            evidence_supporting=row["evidence_supporting"],
            required_capability=row["required_capability"],
            potential_technological_unlock=row["potential_technological_unlock"],
            estimated_economic_value_usd=row["estimated_economic_value_usd"],
            resource_requirements=json.loads(row["resource_requirements"]),
            date_recorded=row["date_recorded"],
            date_last_evaluated=row["date_last_evaluated"],
            reevaluation_triggers=json.loads(row["reevaluation_triggers"]),
            is_resolved=bool(row["is_resolved"]),
            resolution_notes=row["resolution_notes"]
        )
