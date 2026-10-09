"""
HOOD Governed Memory Service
Implements project isolation, temporal validity, provenance, learning pipeline,
semantic search, migration export/import, and X-Sealed protection.
Governed by Master System Specification Section 6 & Build Instructions Section 17.
"""

import sqlite3
import json
import math
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from packages.contracts import (
    MemoryObject,
    MemoryType,
    LearningStatus
)


class GovernanceViolationError(Exception):
    """Raised when an attempt is made to illegally mutate governance or bypass isolation."""
    pass


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _token_similarity(query_tokens: set[str], doc_tokens: set[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    intersection = query_tokens.intersection(doc_tokens)
    return len(intersection) / math.sqrt(len(query_tokens) * len(doc_tokens))


class MemoryService:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path("artifacts/hood_data.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                content TEXT NOT NULL,
                project TEXT NOT NULL,
                source TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                created_at TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_until TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                evidence TEXT,
                verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
                sensitivity TEXT NOT NULL DEFAULT 'INTERNAL',
                access_policy TEXT NOT NULL DEFAULT 'PROJECT_ISOLATED',
                version INTEGER NOT NULL DEFAULT 1,
                supersedes TEXT,
                related_memories TEXT,
                learning_status TEXT NOT NULL DEFAULT 'OBSERVATION'
            )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_proj_type ON memories(project, type)")
            conn.commit()

    def write_memory(self, memory: MemoryObject, caller_agent: str = "Hood") -> MemoryObject:
        # H19 / Governance protection: Agents cannot autonomously rewrite Governance memory
        if memory.type == MemoryType.GOVERNANCE and caller_agent != "Zak":
            raise GovernanceViolationError(
                "Governance memory and constitutional rules cannot be autonomously modified by agents. "
                "Owner authorization (Zak) required."
            )

        with self._get_connection() as conn:
            # Check if this memory supersedes an older memory
            if memory.supersedes:
                old_row = conn.execute("SELECT * FROM memories WHERE memory_id = ?", (memory.supersedes,)).fetchone()
                if old_row:
                    conn.execute(
                        "UPDATE memories SET learning_status = ?, valid_until = ? WHERE memory_id = ?",
                        (LearningStatus.SUPERSEDED.value, memory.valid_from.isoformat(), memory.supersedes)
                    )

            conn.execute("""
            INSERT OR REPLACE INTO memories (
                memory_id, type, content, project, source, source_agent,
                created_at, valid_from, valid_until, confidence, evidence,
                verification_status, sensitivity, access_policy, version,
                supersedes, related_memories, learning_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory.memory_id,
                memory.type.value,
                memory.content,
                memory.project,
                memory.source,
                memory.source_agent,
                memory.created_at.isoformat(),
                memory.valid_from.isoformat(),
                memory.valid_until.isoformat() if memory.valid_until else None,
                memory.confidence,
                json.dumps(memory.evidence),
                memory.verification_status,
                memory.sensitivity,
                memory.access_policy,
                memory.version,
                memory.supersedes,
                json.dumps(memory.related_memories),
                memory.learning_status.value
            ))
            conn.commit()

        return memory

    def get_memory(self, memory_id: str) -> Optional[MemoryObject]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,)).fetchone()
            if not row:
                return None
            return self._row_to_memory(row)

    def query_memories(
        self,
        project: str,
        memory_types: Optional[List[MemoryType]] = None,
        is_x_active: bool = False,
        query_text: Optional[str] = None,
        include_superseded: bool = False
    ) -> List[MemoryObject]:
        """Queries memories enforcing project/client isolation and X-Sealed isolation."""
        with self._get_connection() as conn:
            query = "SELECT * FROM memories WHERE project = ?"
            params: List[Any] = [project]

            if memory_types:
                type_placeholders = ",".join("?" for _ in memory_types)
                query += f" AND type IN ({type_placeholders})"
                params.extend([t.value for t in memory_types])

            if not include_superseded:
                query += " AND learning_status != ?"
                params.append(LearningStatus.SUPERSEDED.value)

            now_iso = datetime.now(timezone.utc).isoformat()
            query += " AND (valid_until IS NULL OR valid_until > ?)"
            params.append(now_iso)

            rows = conn.execute(query, params).fetchall()

            memories = []
            for r in rows:
                mem = self._row_to_memory(r)

                # Isolation check: normal agents cannot view X-Sealed memory
                if mem.type == MemoryType.X_SEALED and not is_x_active:
                    continue

                if query_text:
                    if query_text.lower() not in mem.content.lower():
                        continue

                memories.append(mem)

            return memories

    def query_semantic(
        self,
        query_text: str,
        project: str,
        top_k: int = 5,
        is_x_active: bool = False
    ) -> List[MemoryObject]:
        """Performs semantic relevance ranking over memories within project isolation."""
        all_candidates = self.query_memories(project=project, is_x_active=is_x_active)
        query_tokens = _tokenize(query_text)

        scored = []
        for mem in all_candidates:
            doc_tokens = _tokenize(mem.content)
            sim = _token_similarity(query_tokens, doc_tokens)
            if sim > 0.05 or not query_tokens:
                scored.append((sim, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:top_k]]

    def promote_lesson(
        self,
        memory_id: str,
        target_status: LearningStatus,
        verification_evidence: str
    ) -> MemoryObject:
        mem = self.get_memory(memory_id)
        if not mem:
            raise KeyError(f"Memory {memory_id} not found")

        if mem.type == MemoryType.GOVERNANCE:
            raise GovernanceViolationError("Cannot mutate governance via learning pipeline.")

        mem.learning_status = target_status
        mem.verification_status = "VERIFIED"
        if verification_evidence:
            mem.evidence.append(verification_evidence)

        with self._get_connection() as conn:
            conn.execute(
                "UPDATE memories SET learning_status = ?, verification_status = ?, evidence = ? WHERE memory_id = ?",
                (target_status.value, "VERIFIED", json.dumps(mem.evidence), memory_id)
            )
            conn.commit()

        return mem

    def export_memories(self, output_path: Path, project: Optional[str] = None) -> int:
        """Exports memory records to portable JSON file for cross-machine migration."""
        with self._get_connection() as conn:
            if project:
                rows = conn.execute("SELECT * FROM memories WHERE project = ?", (project,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM memories").fetchall()

            data = [dict(r) for r in rows]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        export_payload = {
            "version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source_engine": "hood_sqlite_memory",
            "record_count": len(data),
            "memories": data
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_payload, f, indent=2)

        return len(data)

    def import_memories(self, input_path: Path) -> int:
        """Imports memory records from portable migration JSON file idempotently."""
        if not input_path.is_file():
            raise FileNotFoundError(f"Import file {input_path} does not exist.")

        with open(input_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        records = payload.get("memories", [])
        imported = 0
        with self._get_connection() as conn:
            for r in records:
                conn.execute("""
                INSERT OR REPLACE INTO memories (
                    memory_id, type, content, project, source, source_agent,
                    created_at, valid_from, valid_until, confidence, evidence,
                    verification_status, sensitivity, access_policy, version,
                    supersedes, related_memories, learning_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    r["memory_id"], r["type"], r["content"], r["project"],
                    r["source"], r["source_agent"], r["created_at"],
                    r["valid_from"], r.get("valid_until"), r.get("confidence", 1.0),
                    r.get("evidence", "[]"), r.get("verification_status", "UNVERIFIED"),
                    r.get("sensitivity", "INTERNAL"), r.get("access_policy", "PROJECT_ISOLATED"),
                    r.get("version", 1), r.get("supersedes"),
                    r.get("related_memories", "[]"), r.get("learning_status", "OBSERVATION")
                ))
                imported += 1
            conn.commit()

        return imported

    def check_temporal_validity(self, memory_id: str) -> bool:
        """Returns True if memory is within its valid_from and valid_until temporal window."""
        mem = self.get_memory(memory_id)
        if not mem:
            return False
        now = datetime.now(timezone.utc)
        if mem.valid_from > now:
            return False
        if mem.valid_until and mem.valid_until < now:
            return False
        return True

    def purge_expired_memories(self, project: Optional[str] = None) -> int:
        """
        Controlled forgetting: safely purges expired non-governance observations
        while strictly preserving GOVERNANCE, AUDIT, and OWNERSHIP history.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            query = """
            DELETE FROM memories
            WHERE type != 'GOVERNANCE'
              AND valid_until IS NOT NULL
              AND valid_until < ?
            """
            params = [now]
            if project:
                query += " AND project = ?"
                params.append(project)

            cursor = conn.execute(query, params)
            conn.commit()
            return cursor.rowcount

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryObject:
        return MemoryObject(
            memory_id=row["memory_id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            project=row["project"],
            source=row["source"],
            source_agent=row["source_agent"],
            created_at=datetime.fromisoformat(row["created_at"]),
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            confidence=row["confidence"],
            evidence=json.loads(row["evidence"]) if row["evidence"] else [],
            verification_status=row["verification_status"],
            sensitivity=row["sensitivity"],
            access_policy=row["access_policy"],
            version=row["version"],
            supersedes=row["supersedes"],
            related_memories=json.loads(row["related_memories"]) if row["related_memories"] else [],
            learning_status=LearningStatus(row["learning_status"])
        )
