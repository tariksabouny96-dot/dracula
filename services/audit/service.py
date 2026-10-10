"""
HOOD Audit & Event Trail Engine
Append-only structured audit system supporting task trace reconstruction and secret redaction.
Governed by Master System Specification Section 13 & Appendix B.
"""

import sqlite3
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from packages.contracts import AuditEvent
from packages.logging.redactor import sanitize_object, redact_string
from packages.config.paths import store_path


class AuditService:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = store_path(db_path, "artifacts/hood_data.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                actor TEXT NOT NULL,
                task_id TEXT,
                project TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                policy_decision TEXT NOT NULL,
                approval_ref TEXT,
                input_refs TEXT,
                tool_or_model TEXT,
                cost REAL DEFAULT 0.0,
                result TEXT,
                artifact_refs TEXT,
                verification TEXT DEFAULT 'PASSED',
                error TEXT,
                correlation_id TEXT NOT NULL
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_events(task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_correlation ON audit_events(correlation_id)")
            conn.commit()

    def record_event(self, event: AuditEvent) -> AuditEvent:
        # Sanitize event inputs, results, and errors
        clean_action = redact_string(event.action)
        clean_target = redact_string(event.target)
        clean_result = redact_string(event.result)
        clean_error = redact_string(event.error) if event.error else None
        clean_inputs = sanitize_object(event.input_refs)

        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO audit_events (
                event_id, timestamp, actor, task_id, project, action, target,
                policy_decision, approval_ref, input_refs, tool_or_model, cost,
                result, artifact_refs, verification, error, correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.event_id,
                event.timestamp.isoformat(),
                event.actor,
                event.task_id,
                event.project,
                clean_action,
                clean_target,
                event.policy_decision,
                event.approval_ref,
                json.dumps(clean_inputs),
                event.tool_or_model,
                event.cost,
                clean_result,
                json.dumps(event.artifact_refs),
                event.verification,
                clean_error,
                event.correlation_id
            ))
            conn.commit()

        return event

    def get_events_for_task(self, task_id: str) -> List[AuditEvent]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM audit_events WHERE task_id = ? ORDER BY timestamp ASC", (task_id,)).fetchall()
            return [self._row_to_event(r) for r in rows]

    def get_events_for_correlation(self, correlation_id: str) -> List[AuditEvent]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM audit_events WHERE correlation_id = ? ORDER BY timestamp ASC", (correlation_id,)).fetchall()
            return [self._row_to_event(r) for r in rows]

    def _row_to_event(self, row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=row["event_id"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            actor=row["actor"],
            task_id=row["task_id"],
            project=row["project"],
            action=row["action"],
            target=row["target"],
            policy_decision=row["policy_decision"],
            approval_ref=row["approval_ref"],
            input_refs=json.loads(row["input_refs"]) if row["input_refs"] else [],
            tool_or_model=row["tool_or_model"],
            cost=row["cost"],
            result=row["result"],
            artifact_refs=json.loads(row["artifact_refs"]) if row["artifact_refs"] else [],
            verification=row["verification"],
            error=row["error"],
            correlation_id=row["correlation_id"]
        )
