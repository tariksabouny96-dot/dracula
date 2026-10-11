"""SQLite persistence for voice calls: history, audit trail, devices, challenges, usage, settings.

One file under ``<data dir>/voicecall/`` (0600). Raw audio is never stored here or anywhere else by Hood.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .contracts import CallDirection, CallRecord, CallState

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
  call_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, direction TEXT NOT NULL, state TEXT NOT NULL,
  room TEXT NOT NULL, device_id TEXT NOT NULL, created REAL NOT NULL, provider TEXT, mode TEXT, language TEXT,
  event_id TEXT, event_type TEXT, briefing TEXT, ringing_at REAL, answered_at REAL, ended_at REAL,
  end_reason TEXT, reservation_id TEXT, reserved_usd REAL DEFAULT 0, cost_usd REAL DEFAULT 0,
  cost_measured INTEGER DEFAULT 0, duration_s REAL DEFAULT 0, audio_in_s REAL DEFAULT 0, audio_out_s REAL DEFAULT 0,
  turns INTEGER DEFAULT 0, last_heartbeat REAL, fallback_sent INTEGER DEFAULT 0, notification_status TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_calls_owner_created ON calls(owner_id, created);
CREATE TABLE IF NOT EXISTS call_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_call_events_call ON call_events(call_id, id);
CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, name TEXT NOT NULL, secret_digest TEXT NOT NULL,
  public_key_b64 TEXT NOT NULL, platform TEXT DEFAULT 'ios', app_version TEXT DEFAULT '',
  created REAL NOT NULL, last_seen REAL, revoked_at REAL, voip_token TEXT, alert_token TEXT, apns_env TEXT DEFAULT 'sandbox'
);
CREATE TABLE IF NOT EXISTS challenges (
  challenge_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL,
  decision TEXT NOT NULL, subject_hash TEXT NOT NULL, nonce TEXT NOT NULL, created REAL NOT NULL,
  expires REAL NOT NULL, used INTEGER DEFAULT 0, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hood_events (
  event_id TEXT PRIMARY KEY, type TEXT NOT NULL, severity TEXT NOT NULL, subject TEXT NOT NULL, summary TEXT NOT NULL,
  source TEXT, created REAL NOT NULL, decision TEXT, call_id TEXT, notification_status TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_hood_events_subject ON hood_events(subject, created);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
  amount REAL NOT NULL, usd REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_call ON usage(call_id);
CREATE TABLE IF NOT EXISTS pairing_codes (
  code_digest TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, used INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pairing_failures (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (owner_id TEXT PRIMARY KEY, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts_sent (key TEXT PRIMARY KEY, ts REAL NOT NULL);
"""

_CALL_COLS = ("call_id owner_id direction state room device_id created provider mode language event_id event_type "
              "briefing ringing_at answered_at ended_at end_reason reservation_id reserved_usd cost_usd cost_measured "
              "duration_s audio_in_s audio_out_s turns last_heartbeat fallback_sent notification_status").split()


class VoiceStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "voicecall"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.dir / "voicecall.sqlite3"
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript(SCHEMA)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
            finally:
                conn.close()

    # ------------------------------------------------------------------ calls
    @staticmethod
    def _row_to_call(r: sqlite3.Row) -> CallRecord:
        d = {k: r[k] for k in _CALL_COLS}
        d["direction"] = CallDirection(d["direction"])
        d["state"] = CallState(d["state"])
        d["cost_measured"] = bool(d["cost_measured"])
        d["fallback_sent"] = bool(d["fallback_sent"])
        for k in ("provider", "mode", "language", "event_id", "event_type", "briefing", "end_reason",
                  "reservation_id", "notification_status"):
            d[k] = d[k] or ""
        for k in ("reserved_usd", "cost_usd", "duration_s", "audio_in_s", "audio_out_s"):
            d[k] = d[k] or 0.0
        d["turns"] = d["turns"] or 0
        return CallRecord(**d)

    def insert_call(self, c: CallRecord) -> None:
        vals = []
        for k in _CALL_COLS:
            v = getattr(c, k)
            vals.append(v.value if hasattr(v, "value") else (int(v) if isinstance(v, bool) else v))
        with self._db() as db:
            db.execute(f"INSERT INTO calls ({','.join(_CALL_COLS)}) VALUES ({','.join('?' * len(_CALL_COLS))})", vals)

    def update_call(self, call_id: str, **fields: Any) -> None:
        if not fields:
            return
        for k in fields:
            if k not in _CALL_COLS or k == "call_id":
                raise KeyError(k)
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{k}=?")
            vals.append(v.value if hasattr(v, "value") else (int(v) if isinstance(v, bool) else v))
        with self._db() as db:
            db.execute(f"UPDATE calls SET {','.join(sets)} WHERE call_id=?", [*vals, call_id])

    def get_call(self, call_id: str) -> Optional[CallRecord]:
        with self._db() as db:
            r = db.execute("SELECT * FROM calls WHERE call_id=?", (call_id,)).fetchone()
        return self._row_to_call(r) if r else None

    def list_calls(self, owner_id: str, limit: int = 50) -> List[CallRecord]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM calls WHERE owner_id=? ORDER BY created DESC LIMIT ?",
                              (owner_id, limit)).fetchall()
        return [self._row_to_call(r) for r in rows]

    def active_calls(self, owner_id: Optional[str] = None) -> List[CallRecord]:
        q = "SELECT * FROM calls WHERE state IN ('CREATED','RINGING','CONNECTING','ACTIVE')"
        args: tuple = ()
        if owner_id:
            q += " AND owner_id=?"
            args = (owner_id,)
        with self._db() as db:
            return [self._row_to_call(r) for r in db.execute(q + " ORDER BY created", args).fetchall()]

    def calls_since(self, owner_id: str, since: float, direction: Optional[CallDirection] = None) -> List[CallRecord]:
        q, args = "SELECT * FROM calls WHERE owner_id=? AND created>=?", [owner_id, since]
        if direction:
            q += " AND direction=?"
            args.append(direction.value)
        with self._db() as db:
            return [self._row_to_call(r) for r in db.execute(q, args).fetchall()]

    def totals_since(self, owner_id: str, since: float) -> Dict[str, float]:
        """Cost and minutes of all calls (including failed and abandoned ones) created since ``since``."""
        with self._db() as db:
            r = db.execute("SELECT COALESCE(SUM(cost_usd),0) c, COALESCE(SUM(duration_s),0) d, COUNT(*) n, "
                           "COALESCE(SUM(reserved_usd),0) rsv FROM calls WHERE owner_id=? AND created>=? "
                           "AND state NOT IN ('CREATED','RINGING','CONNECTING','ACTIVE')", (owner_id, since)).fetchone()
            live = db.execute("SELECT COALESCE(SUM(reserved_usd),0) rsv FROM calls WHERE owner_id=? AND created>=? "
                              "AND state IN ('CREATED','RINGING','CONNECTING','ACTIVE')", (owner_id, since)).fetchone()
        return {"usd": float(r["c"]), "minutes": float(r["d"]) / 60.0, "calls": int(r["n"]),
                "reserved_open_usd": float(live["rsv"])}

    # ------------------------------------------------------------------ audit trail
    def add_event(self, call_id: str, kind: str, detail: Optional[Dict[str, Any]] = None) -> int:
        with self._db() as db:
            cur = db.execute("INSERT INTO call_events (call_id, ts, kind, detail) VALUES (?,?,?,?)",
                             (call_id, time.time(), kind, json.dumps(detail or {}, sort_keys=True)))
            return int(cur.lastrowid)

    def events_after(self, call_id: str, after_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT id, ts, kind, detail FROM call_events WHERE call_id=? AND id>? ORDER BY id LIMIT ?",
                              (call_id, after_id, limit)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "detail": json.loads(r["detail"] or "{}")} for r in rows]

    # ------------------------------------------------------------------ usage
    def add_usage(self, call_id: str, kind: str, amount: float, usd: float = 0.0) -> None:
        with self._db() as db:
            db.execute("INSERT INTO usage (call_id, ts, kind, amount, usd) VALUES (?,?,?,?,?)",
                       (call_id, time.time(), kind, float(amount), float(usd)))

    def usage_sum(self, call_id: str) -> Dict[str, float]:
        with self._db() as db:
            rows = db.execute("SELECT kind, SUM(amount) a, SUM(usd) u FROM usage WHERE call_id=? GROUP BY kind",
                              (call_id,)).fetchall()
        out: Dict[str, float] = {}
        for r in rows:
            out[r["kind"]] = float(r["a"])
            out[r["kind"] + "_usd"] = float(r["u"])
        return out

    # ------------------------------------------------------------------ hood events (dedupe + history)
    def record_event_decision(self, event_id: str, type_: str, severity: str, subject: str, summary: str,
                              source: str, decision: str, call_id: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO hood_events (event_id,type,severity,subject,summary,source,created,decision,call_id) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (event_id, type_, severity, subject, summary, source,
                                                       time.time(), decision, call_id))

    def last_event_for_subject(self, subject: str, since: float) -> Optional[Dict[str, Any]]:
        with self._db() as db:
            r = db.execute("SELECT * FROM hood_events WHERE subject=? AND created>=? ORDER BY created DESC LIMIT 1",
                           (subject, since)).fetchone()
        return dict(r) if r else None

    def recent_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM hood_events ORDER BY created DESC LIMIT ?", (limit,)).fetchall()]

    def set_event_notification(self, event_id: str, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE hood_events SET notification_status=? WHERE event_id=?", (status, event_id))

    # ------------------------------------------------------------------ settings, alerts
    def get_settings(self, owner_id: str) -> Dict[str, Any]:
        with self._db() as db:
            r = db.execute("SELECT json FROM settings WHERE owner_id=?", (owner_id,)).fetchone()
        return json.loads(r["json"]) if r else {}

    def put_settings(self, owner_id: str, data: Dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO settings (owner_id, json) VALUES (?,?)", (owner_id, json.dumps(data, sort_keys=True)))

    def alert_once(self, key: str) -> bool:
        """True the first time ``key`` is seen (used so each spend threshold alerts once per period)."""
        with self._db() as db:
            try:
                db.execute("INSERT INTO alerts_sent (key, ts) VALUES (?,?)", (key, time.time()))
                return True
            except sqlite3.IntegrityError:
                return False
