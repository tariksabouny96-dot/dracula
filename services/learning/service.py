"""Self-learning: HOOD turns experience into durable, trust-ranked lessons.

Every lesson lives in governed memory and climbs the trust ladder as evidence
accumulates:

    OBSERVATION -> CANDIDATE -> PROVISIONAL -> ESTABLISHED

HOOD reinforces a lesson automatically as it recurs, but it can only raise a
lesson to CANDIDATE and PROVISIONAL on its own. **ESTABLISHED — the level HOOD
is allowed to rely on as settled truth — requires the Root Owner.** The owner is
always the final authority over what HOOD "knows".

This builds on the hardened memory trust ladder (F25): promotion is forward-only,
needs evidence, and ESTABLISHED is owner-gated at the memory layer too.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from packages.contracts import LearningStatus, MemoryObject, MemoryType
from packages.security import StopLatch

CANDIDATE_AT = 3      # observations to auto-promote OBSERVATION -> CANDIDATE
PROVISIONAL_AT = 6    # observations to auto-promote CANDIDATE -> PROVISIONAL
LEARNING_PROJECT = "learning"
OWNER = "Zak"


def _signature(category: str, lesson: str) -> str:
    return hashlib.sha1(f"{category.strip().lower()}|{lesson.strip().lower()}".encode()).hexdigest()


class LearningService:
    def __init__(self, memory_service, approval_service=None, stop_latch: Optional[StopLatch] = None,
                 data_dir: Optional[Path] = None):
        self.memory = memory_service
        self.approval_service = approval_service
        self.stop_latch = stop_latch or StopLatch()
        base = Path(data_dir) if data_dir else Path(
            os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "learning"
        base.mkdir(parents=True, exist_ok=True)
        self.db_path = base / "learning.db"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS lessons(
                lesson_id TEXT PRIMARY KEY, principal TEXT NOT NULL, memory_id TEXT NOT NULL,
                category TEXT NOT NULL, content TEXT NOT NULL, observations INTEGER NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lessons_principal ON lessons(principal)")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------- record
    def record_outcome(self, principal: str, category: str, lesson: str,
                       evidence: str = "", confidence: float = 0.6) -> Dict[str, Any]:
        """Record (or reinforce) a lesson learned from experience.

        The first sighting creates an OBSERVATION; repeated sightings of the same
        lesson accumulate and auto-promote through CANDIDATE and PROVISIONAL.
        """
        self.stop_latch.check()
        if not lesson or not lesson.strip():
            raise ValueError("lesson text is required")
        sig = _signature(category, lesson)
        lesson_id = "lesson_" + hashlib.sha1(f"{principal}:{sig}".encode()).hexdigest()[:16]
        mem_id = "mem_" + lesson_id
        now = time.time()

        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
            if row is None:
                mem = MemoryObject(
                    memory_id=mem_id, type=MemoryType.EXPERIENCE, content=lesson,
                    project=LEARNING_PROJECT, principal=principal, source="self_learning",
                    source_agent="Hood", confidence=max(0.0, min(confidence, 1.0)),
                    evidence=[evidence] if evidence else [], learning_status=LearningStatus.OBSERVATION)
                self.memory.write_memory(mem, caller_agent="Hood")
                conn.execute("INSERT INTO lessons VALUES (?,?,?,?,?,?,?,?,?)",
                             (lesson_id, principal, mem_id, category, lesson, 1,
                              LearningStatus.OBSERVATION.value, now, now))
                conn.commit()
                observations, status = 1, LearningStatus.OBSERVATION
            else:
                observations = row["observations"] + 1
                status = LearningStatus(row["status"])
                conn.execute("UPDATE lessons SET observations=?, updated_at=? WHERE lesson_id=?",
                             (observations, now, lesson_id))
                conn.commit()

        # Auto-promote by accumulated evidence, but never to ESTABLISHED.
        target = status
        if observations >= PROVISIONAL_AT:
            target = LearningStatus.PROVISIONAL
        elif observations >= CANDIDATE_AT:
            target = LearningStatus.CANDIDATE
        if _rank(target) > _rank(status):
            self.memory.promote_lesson(
                mem_id, target, f"reinforced by {observations} observations ({category})",
                promoted_by="hood")
            with self._lock, self._conn() as conn:
                conn.execute("UPDATE lessons SET status=? WHERE lesson_id=?", (target.value, lesson_id))
                conn.commit()
            status = target
        return self.get(lesson_id)

    # ------------------------------------------------------------- owner gate
    def establish(self, lesson_id: str, approver: str = OWNER, is_root_owner: bool = False) -> Dict[str, Any]:
        """Promote a lesson to ESTABLISHED (settled truth HOOD may rely on).
        Owner authority only — this is the final-authority gate on learning."""
        if not is_root_owner:
            raise PermissionError("Only the Root Owner may establish a lesson as settled truth.")
        row = self._row(lesson_id)
        if LearningStatus(row["status"]) != LearningStatus.PROVISIONAL:
            raise ValueError(f"lesson is {row['status']}; only a PROVISIONAL lesson can be established")
        # promote_lesson enforces ESTABLISHED requires promoted_by == owner.
        self.memory.promote_lesson(row["memory_id"], LearningStatus.ESTABLISHED,
                                   f"established by owner {approver}", promoted_by=OWNER)
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE lessons SET status=?, updated_at=? WHERE lesson_id=?",
                         (LearningStatus.ESTABLISHED.value, time.time(), lesson_id))
            conn.commit()
        return self.get(lesson_id)

    # ------------------------------------------------------------- recall
    def recall(self, principal: str, query: Optional[str] = None,
               min_status: LearningStatus = LearningStatus.CANDIDATE) -> List[Dict[str, Any]]:
        """Return trusted lessons for this principal, most-trusted first."""
        floor = _rank(min_status)
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM lessons WHERE principal=?", (principal,)).fetchall()
        out = [self._view(r) for r in rows if _rank(LearningStatus(r["status"])) >= floor]
        if query:
            q = query.lower()
            out = [v for v in out if q in v["content"].lower() or q in v["category"].lower()]
        out.sort(key=lambda v: (_rank(LearningStatus(v["status"])), v["observations"]), reverse=True)
        return out

    def pending_promotions(self, principal: str) -> List[Dict[str, Any]]:
        """PROVISIONAL lessons awaiting the owner's decision to establish."""
        return [v for v in self.list(principal) if v["status"] == LearningStatus.PROVISIONAL.value]

    def list(self, principal: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM lessons WHERE principal=? ORDER BY updated_at DESC",
                                (principal,)).fetchall()
        return [self._view(r) for r in rows]

    def get(self, lesson_id: str) -> Dict[str, Any]:
        return self._view(self._row(lesson_id))

    def _row(self, lesson_id: str) -> sqlite3.Row:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM lessons WHERE lesson_id=?", (lesson_id,)).fetchone()
        if not row:
            raise KeyError(lesson_id)
        return row

    @staticmethod
    def _view(r) -> Dict[str, Any]:
        return {"lesson_id": r["lesson_id"], "principal": r["principal"], "category": r["category"],
                "content": r["content"], "observations": r["observations"], "status": r["status"],
                "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _rank(status: LearningStatus) -> int:
    return {LearningStatus.SUPERSEDED: -1, LearningStatus.OBSERVATION: 0, LearningStatus.CANDIDATE: 1,
            LearningStatus.PROVISIONAL: 2, LearningStatus.ESTABLISHED: 3}.get(status, 0)
