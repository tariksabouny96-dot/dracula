"""Durable, per-principal conversation history (SQLite).

Chat sessions are keyed by the server-derived id ``user:<user_id>``, so one
principal can never load another's messages. History survives restarts and can
be deleted on request (retention/erasure).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import List

HISTORY_LIMIT = 200


class ConversationStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                session_id TEXT NOT NULL, seq INTEGER NOT NULL, id TEXT NOT NULL, sender TEXT NOT NULL,
                modality TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL, speaker_id TEXT NOT NULL,
                approval_ref TEXT, PRIMARY KEY (session_id, seq))""")

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def append(self, session_id: str, message) -> None:
        with self._lock, self._connect() as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id=?",
                             (session_id,)).fetchone()[0]
            db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
                       (session_id, seq, message.id, message.sender, message.modality, message.text,
                        message.timestamp, message.speaker_id, message.approval_ref))

    def load(self, session_id: str, limit: int = HISTORY_LIMIT) -> List[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM (SELECT * FROM messages WHERE session_id=? ORDER BY seq DESC LIMIT ?) "
                              "ORDER BY seq", (session_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def delete(self, session_id: str) -> int:
        with self._lock, self._connect() as db:
            return db.execute("DELETE FROM messages WHERE session_id=?", (session_id,)).rowcount
