"""Hood multi-agent engine: durable planning, scheduling, governed execution and
independent verification.

Flow (each arrow is a persisted state change):

    create  -> PLANNING -> AWAITING_PLAN_APPROVAL      (planner model call, plan validated)
    approve -> QUEUED                                   (owner approves the exact plan hash + budget)
    step    -> RUNNING: one ready task per step under a lease with a fencing token
               engineer/qa: model proposal persisted first, then applied in the sandbox
               reviewer:    findings recorded (advisory, cannot pass or fail a mission)
            -> VERIFYING: deterministic verifier runs compile + unit + independent QA tests
               PASS -> artifact zip hashed and registered -> COMPLETED
               FAIL -> repair task with the failure report (bounded) or FAILED
               no usable evidence -> UNVERIFIED
    cancel  -> CANCELLED (running processes killed; late results rejected by fence)
    stop    -> emergency stop latch: no new step, processes killed, running work BLOCKED

Nothing is reported COMPLETED unless the verifier observed passing checks on the
exact workspace contents that were packaged.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from packages.contracts import ModelRequest, ModelResponse
from packages.security import StopLatch, EmergencyStopActive
from .contracts import (SCHEMA_VERSION, TERMINAL_MISSION_STATES, AgentRole, AgentWorkProduct, MissionPlan,
                        MissionState, PlannedTask, ReviewReport, TaskState, VerificationDecision,
                        VerificationVerdict)
from .planner import PlanRejected, plan_mission
from .sandbox import SandboxUnavailable, SandboxViolation, Workspace
from .specialists import AgentOutputRejected, run_specialist
from .verifier import verify
from services.model_gateway.cost_controller import BudgetExceededError
from services.model_gateway.base import ProviderNotConfiguredError

LEASE_SECONDS = 600
MAX_REPAIRS = 2
MAX_TASK_ATTEMPTS = 3


class MissionConflict(RuntimeError):
    """The requested transition is not allowed in the mission's current state."""


class MissionBudgetExceeded(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


class AgentEngine:
    def __init__(self, root: Path, *, router: Any = None,
                 invoke: Optional[Callable[[ModelRequest], ModelResponse]] = None,
                 stop_latch: Optional[StopLatch] = None, allow_simulated: bool = False,
                 require_network_isolation: bool = True, worker_id: Optional[str] = None):
        """``router`` is a ModelRouter (live providers, budgets, pricing).

        ``invoke`` replaces the router for deterministic contract tests; its
        responses must be marked ``is_mock`` and are refused unless
        ``allow_simulated`` is set, in which case every record is labelled SIMULATED.
        """
        if router is None and invoke is None:
            raise ValueError("A model router or invoke function is required")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "workspaces").mkdir(exist_ok=True, mode=0o700)
        (self.root / "artifacts").mkdir(exist_ok=True, mode=0o700)
        self.db_path = self.root / "agent_engine.sqlite3"
        self.router = router
        self._invoke = invoke or router.invoke
        self.stop_latch = stop_latch or StopLatch()
        self.allow_simulated = allow_simulated
        self.require_network_isolation = require_network_isolation
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._workspaces: Dict[str, Workspace] = {}
        self._ws_lock = threading.Lock()
        self._receipt_key = self._load_receipt_key()
        self._init_db()

    # ================================================================ storage
    @contextmanager
    def _db(self):
        """Autocommit connection; explicit BEGIN IMMEDIATE for multi-row transitions.

        Closing on exit rolls back any transaction left open by an exception.
        """
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def _init_db(self):
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, objective TEXT NOT NULL, state TEXT NOT NULL,
                plan TEXT, plan_sha256 TEXT, budget_usd REAL NOT NULL, approved_by TEXT, approved_at TEXT,
                repairs INTEGER NOT NULL DEFAULT 0, provider_mode TEXT NOT NULL DEFAULT 'NONE',
                verdict TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL, schema_version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                mission_id TEXT NOT NULL REFERENCES missions(id), task_id TEXT NOT NULL, seq INTEGER NOT NULL,
                role TEXT NOT NULL, title TEXT NOT NULL, instructions TEXT NOT NULL, depends_on TEXT NOT NULL,
                state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_expires TEXT,
                fence INTEGER NOT NULL DEFAULT 0, proposal TEXT, result TEXT, failure_context TEXT, error TEXT,
                updated TEXT NOT NULL, PRIMARY KEY (mission_id, task_id));
            CREATE TABLE IF NOT EXISTS spend (
                call_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id), task_id TEXT,
                reserved_usd REAL NOT NULL, actual_usd REAL, measured INTEGER, provider TEXT, model TEXT,
                simulated INTEGER, created TEXT NOT NULL, settled TEXT);
            CREATE TABLE IF NOT EXISTS receipts (
                id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id), task_id TEXT,
                kind TEXT NOT NULL, body TEXT NOT NULL, mac TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id), owner TEXT NOT NULL,
                name TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                mime TEXT NOT NULL, workspace_sha256 TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL, ts TEXT NOT NULL,
                actor TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
            """)

    def _event(self, db, mission_id: str, actor: str, kind: str, detail: Any = ""):
        db.execute("INSERT INTO events (mission_id, ts, actor, kind, detail) VALUES (?,?,?,?,?)",
                   (mission_id, _now(), actor, kind, json.dumps(detail, default=str)[:4000]))

    def _load_receipt_key(self) -> bytes:
        env = os.environ.get("HOOD_RECEIPT_KEY")
        if env:
            return env.encode("utf-8")
        key_file = self.root / ".receipt_key"
        if not key_file.exists():
            with os.fdopen(os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as fh:
                fh.write(secrets.token_bytes(32))
        if os.name == "posix" and key_file.stat().st_mode & 0o077:
            raise PermissionError(f"Receipt key {key_file} must not be readable by other users")
        return key_file.read_bytes()

    def _receipt(self, db, mission_id: str, task_id: Optional[str], kind: str, body: Dict[str, Any]) -> str:
        """Integrity-protected record of an action or decision. Agents never write receipts."""
        rid = "rcp_" + uuid.uuid4().hex
        body = {"schema_version": SCHEMA_VERSION, "receipt_id": rid, "mission_id": mission_id,
                "task_id": task_id, "kind": kind, "actor": self.worker_id, "at": _now(), **body}
        mac = hmac.new(self._receipt_key, _canonical(body), hashlib.sha256).hexdigest()
        db.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?,?)",
                   (rid, mission_id, task_id, kind, json.dumps(body, sort_keys=True), mac, body["at"]))
        return rid

    def verify_receipts(self, owner: str, mission_id: str) -> Dict[str, Any]:
        self._mission(owner, mission_id)
        bad = []
        with self._db() as db:
            rows = db.execute("SELECT id, body, mac FROM receipts WHERE mission_id=?", (mission_id,)).fetchall()
        for row in rows:
            expected = hmac.new(self._receipt_key, _canonical(json.loads(row["body"])), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, row["mac"]):
                bad.append(row["id"])
        return {"count": len(rows), "invalid": bad, "valid": not bad}

    def _mission(self, owner: str, mission_id: str, db=None) -> sqlite3.Row:
        if not isinstance(mission_id, str) or not mission_id.startswith("agm_"):
            raise KeyError("Mission not found")
        if db is None:
            with self._db() as conn:
                row = conn.execute("SELECT * FROM missions WHERE id=? AND owner=?", (mission_id, owner)).fetchone()
        else:
            row = db.execute("SELECT * FROM missions WHERE id=? AND owner=?", (mission_id, owner)).fetchone()
        if not row:
            raise KeyError("Mission not found")  # same answer for "absent" and "not yours"
        return row

    def _set_state(self, db, mission_id: str, state: MissionState, actor: str, **fields):
        cols = ", ".join(f"{k}=?" for k in fields)
        db.execute(f"UPDATE missions SET state=?, updated=?{', ' + cols if cols else ''} WHERE id=?",
                   (state.value, _now(), *fields.values(), mission_id))
        self._event(db, mission_id, actor, "STATE", {"state": state.value, **fields})

    def _workspace(self, mission_id: str) -> Workspace:
        with self._ws_lock:
            ws = self._workspaces.get(mission_id)
            if ws is None:
                ws = Workspace(self.root / "workspaces" / mission_id, self.stop_latch,
                               require_network_isolation=self.require_network_isolation)
                self._workspaces[mission_id] = ws
            return ws

    # ================================================================ model calls
    def _call_model(self, mission: sqlite3.Row, task_id: Optional[str], request: ModelRequest) -> ModelResponse:
        """Budget-checked model call with a durable spend reservation.

        The reservation row is written before the request; if the process dies
        mid-call the row stays unsettled and is charged at its reserved amount.
        """
        self.stop_latch.check()
        estimate = self.router.estimate_max_cost(request) if self.router is not None else 0.0
        call_id = "call_" + uuid.uuid4().hex
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            spent = db.execute("SELECT COALESCE(SUM(COALESCE(actual_usd, reserved_usd)),0) FROM spend "
                               "WHERE mission_id=?", (mission["id"],)).fetchone()[0]
            if estimate == float("inf") or spent + estimate > mission["budget_usd"]:
                db.execute("ROLLBACK")
                raise MissionBudgetExceeded(
                    f"Mission budget ${mission['budget_usd']:.4f} would be exceeded "
                    f"(spent ${spent:.4f}, next call up to {'unknown' if estimate == float('inf') else f'${estimate:.4f}'})")
            db.execute("INSERT INTO spend (call_id, mission_id, task_id, reserved_usd, created) VALUES (?,?,?,?,?)",
                       (call_id, mission["id"], task_id, estimate, _now()))
            db.execute("COMMIT")
        try:
            response = self._invoke(request)
        except (ProviderNotConfiguredError, BudgetExceededError):
            with self._db() as db:  # refused before sending: nothing was spent
                db.execute("UPDATE spend SET actual_usd=0, measured=1, settled=? WHERE call_id=?", (_now(), call_id))
            raise
        except Exception:
            with self._db() as db:  # a failed call may still have been billed: keep the reservation
                db.execute("UPDATE spend SET settled=?, measured=0 WHERE call_id=?", (_now(), call_id))
            raise
        if response.is_mock and not self.allow_simulated:
            raise AgentOutputRejected("Simulated model output is not accepted by the live engine")
        actual, measured = None, 0
        if self.router is not None:
            history = [c for c in self.router.cost_controller.get_call_history(request.task_id)]
            if history:
                actual, measured = history[-1]["cost_usd"], int(bool(history[-1].get("cost_measured", True)))
        provider = getattr(response.provider, "value", str(response.provider))
        with self._db() as db:
            db.execute("UPDATE spend SET actual_usd=?, measured=?, provider=?, model=?, simulated=?, settled=? "
                       "WHERE call_id=?", (actual if actual is not None else 0.0 if response.is_mock else estimate,
                                           measured, provider, response.model_name, int(response.is_mock),
                                           _now(), call_id))
            mode = "SIMULATED" if response.is_mock else "LIVE"
            current = db.execute("SELECT provider_mode FROM missions WHERE id=?", (mission["id"],)).fetchone()[0]
            if current != mode:
                db.execute("UPDATE missions SET provider_mode=? WHERE id=?",
                           ("MIXED" if current not in ("NONE", mode) else mode, mission["id"]))
        return response

    def spend(self, owner: str, mission_id: str) -> Dict[str, Any]:
        self._mission(owner, mission_id)
        with self._db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM spend WHERE mission_id=? ORDER BY created",
                                                (mission_id,))]
        total = sum((r["actual_usd"] if r["actual_usd"] is not None else r["reserved_usd"]) for r in rows)
        return {"calls": rows, "total_usd": round(total, 6),
                "unsettled_calls": sum(1 for r in rows if r["settled"] is None)}

    # ================================================================ lifecycle
    def create_mission(self, owner: str, objective: str, budget_usd: float = 1.0) -> Dict[str, Any]:
        if not isinstance(owner, str) or not owner:
            raise ValueError("Authenticated owner required")
        if not isinstance(objective, str) or not 10 <= len(objective.strip()) <= 8000:
            raise ValueError("Objective must be 10-8000 characters")
        if not isinstance(budget_usd, (int, float)) or not 0 <= budget_usd <= 100:
            raise ValueError("Budget must be between 0 and 100 USD")
        self.stop_latch.check()
        mission_id = "agm_" + uuid.uuid4().hex
        with self._db() as db:
            db.execute("INSERT INTO missions (id, owner, objective, state, budget_usd, created, updated, "
                       "schema_version) VALUES (?,?,?,?,?,?,?,?)",
                       (mission_id, owner, objective.strip(), MissionState.PLANNING.value, float(budget_usd),
                        _now(), _now(), SCHEMA_VERSION))
            self._event(db, mission_id, owner, "CREATED", {"budget_usd": budget_usd})
        mission = self._mission(owner, mission_id)
        try:
            plan, response = plan_mission(objective.strip(),
                                          lambda req: self._call_model(mission, None, req), mission_id=mission_id)
        except (PlanRejected, AgentOutputRejected, MissionBudgetExceeded, BudgetExceededError,
                EmergencyStopActive) as exc:
            with self._db() as db:
                self._set_state(db, mission_id, MissionState.BLOCKED, "planner", error=f"Planning failed: {exc}")
            return self.status(owner, mission_id)
        except Exception as exc:  # provider outage etc.: no fallback to fabricated plans
            with self._db() as db:
                self._set_state(db, mission_id, MissionState.BLOCKED, "planner",
                                error=f"Planner provider unavailable: {str(exc)[:300]}")
            return self.status(owner, mission_id)
        plan_json = plan.model_dump(mode="json")
        plan_sha = hashlib.sha256(_canonical(plan_json)).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for seq, task in enumerate(plan.tasks):
                db.execute("INSERT INTO tasks (mission_id, task_id, seq, role, title, instructions, depends_on, "
                           "state, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                           (mission_id, task.id, seq, task.role.value, task.title, task.instructions,
                            json.dumps(task.depends_on), TaskState.CREATED.value, _now()))
            self._receipt(db, mission_id, None, "PLAN", {
                "plan_sha256": plan_sha, "provider": getattr(response.provider, "value", str(response.provider)),
                "model": response.model_name, "simulated": response.is_mock})
            self._set_state(db, mission_id, MissionState.AWAITING_PLAN_APPROVAL, "planner",
                            plan=json.dumps(plan_json), plan_sha256=plan_sha)
            db.execute("COMMIT")
        return self.status(owner, mission_id)

    def approve_plan(self, owner: str, mission_id: str, plan_sha256: str, approver: str) -> Dict[str, Any]:
        """Approval binds to the exact plan hash, budget, workspace and engine command allowlist."""
        self.stop_latch.check()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            mission = self._mission(owner, mission_id, db)
            if mission["state"] != MissionState.AWAITING_PLAN_APPROVAL.value:
                db.execute("ROLLBACK")
                raise MissionConflict(f"Mission is {mission['state']}, not awaiting plan approval")
            if not isinstance(plan_sha256, str) or not hmac.compare_digest(plan_sha256, mission["plan_sha256"]):
                db.execute("ROLLBACK")
                raise MissionConflict("Approval does not match the current plan")
            db.execute("UPDATE tasks SET state=?, updated=? WHERE mission_id=? AND state=?",
                       (TaskState.QUEUED.value, _now(), mission_id, TaskState.CREATED.value))
            self._receipt(db, mission_id, None, "PLAN_APPROVAL", {
                "plan_sha256": plan_sha256, "approved_by": approver, "budget_usd": mission["budget_usd"],
                "scope": {"workspace": f"workspaces/{mission_id}", "network": "none",
                          "commands": ["python -m compileall app", "python -m pytest tests", "python -m pytest qa_tests"],
                          "role_write_roots": {"engineer": ["app", "tests"], "qa": ["qa_tests"], "reviewer": []}}})
            self._set_state(db, mission_id, MissionState.QUEUED, approver, approved_by=approver, approved_at=_now())
            db.execute("COMMIT")
        return self.status(owner, mission_id)

    def cancel(self, owner: str, mission_id: str, actor: str) -> Dict[str, Any]:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            mission = self._mission(owner, mission_id, db)
            if MissionState(mission["state"]) in TERMINAL_MISSION_STATES:
                db.execute("ROLLBACK")
                raise MissionConflict(f"Mission already {mission['state']}")
            db.execute("UPDATE tasks SET state=?, fence=fence+1, lease_owner=NULL, updated=? WHERE mission_id=? "
                       "AND state IN (?,?,?,?)", (TaskState.CANCELLED.value, _now(), mission_id,
                                                  TaskState.CREATED.value, TaskState.QUEUED.value,
                                                  TaskState.RUNNING.value, TaskState.BLOCKED.value))
            self._set_state(db, mission_id, MissionState.CANCELLED, actor)
            db.execute("COMMIT")
        killed = self._workspace(mission_id).kill_all()
        with self._db() as db:
            self._event(db, mission_id, actor, "PROCESSES_KILLED", {"count": killed})
        return self.status(owner, mission_id)

    def retry_blocked(self, owner: str, mission_id: str, actor: str) -> Dict[str, Any]:
        """Operator decision to retry a BLOCKED mission (e.g. provider outage, raised budget).

        Blocked tasks are re-queued; a task whose model proposal was already persisted
        re-applies that proposal instead of calling the model again.
        """
        self.stop_latch.check()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            mission = self._mission(owner, mission_id, db)
            if mission["state"] != MissionState.BLOCKED.value:
                db.execute("ROLLBACK")
                raise MissionConflict(f"Mission is {mission['state']}; only BLOCKED missions can be retried")
            if not mission["approved_by"]:
                db.execute("ROLLBACK")
                raise MissionConflict("Mission was blocked before plan approval; create a new mission")
            requeued = db.execute("UPDATE tasks SET state=?, fence=fence+1, lease_owner=NULL, error=NULL, "
                                  "updated=? WHERE mission_id=? AND state=?",
                                  (TaskState.QUEUED.value, _now(), mission_id, TaskState.BLOCKED.value)).rowcount
            done = db.execute("SELECT COUNT(*) FROM tasks WHERE mission_id=? AND state=?",
                              (mission_id, TaskState.COMPLETED.value)).fetchone()[0]
            self._receipt(db, mission_id, None, "OPERATOR_RETRY", {"retried_by": actor, "tasks_requeued": requeued,
                                                                   "previous_error": mission["error"]})
            self._set_state(db, mission_id, MissionState.RUNNING if done else MissionState.QUEUED, actor, error=None)
            db.execute("COMMIT")
        return self.status(owner, mission_id)

    def halt_all(self) -> Dict[str, Any]:
        """Emergency-stop hook: kill every sandbox process and block in-flight work."""
        with self._ws_lock:
            killed = sum(ws.kill_all() for ws in self._workspaces.values())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT DISTINCT mission_id FROM tasks WHERE state=?", (TaskState.RUNNING.value,)).fetchall()
            db.execute("UPDATE tasks SET state=?, fence=fence+1, lease_owner=NULL, error=?, updated=? WHERE state=?",
                       (TaskState.BLOCKED.value, "Emergency stop", _now(), TaskState.RUNNING.value))
            for row in rows:
                self._set_state(db, row["mission_id"], MissionState.BLOCKED, "EMERGENCY_STOP",
                                error="Emergency stop during execution; operator decision required")
            db.execute("COMMIT")
        return {"processes_killed": killed, "missions_blocked": len(rows)}

    def recover(self) -> Dict[str, Any]:
        """Startup reconciliation after a crash. Never blindly replays side effects.

        - RUNNING tasks with an expired lease: if their model proposal was already
          persisted, re-queue so the *same* proposal is re-applied (idempotent full-file
          writes, no new model call); otherwise re-queue as a new attempt, or BLOCK once
          the attempt limit is reached.
        - Unsettled spend reservations are charged at their reserved amount.
        - Missions stuck in VERIFYING are re-verified (read-only checks).
        """
        now = datetime.now(timezone.utc)
        requeued, blocked = [], []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for t in db.execute("SELECT * FROM tasks WHERE state=?", (TaskState.RUNNING.value,)).fetchall():
                if t["lease_expires"] and datetime.fromisoformat(t["lease_expires"]) > now:
                    continue
                if t["proposal"] is None and t["attempts"] >= MAX_TASK_ATTEMPTS:
                    db.execute("UPDATE tasks SET state=?, fence=fence+1, lease_owner=NULL, error=?, updated=? "
                               "WHERE mission_id=? AND task_id=?", (TaskState.BLOCKED.value,
                               "Interrupted repeatedly; operator decision required", _now(), t["mission_id"], t["task_id"]))
                    self._set_state(db, t["mission_id"], MissionState.BLOCKED, "recovery",
                                    error=f"Task {t['task_id']} interrupted {t['attempts']} times")
                    blocked.append(t["task_id"])
                else:
                    db.execute("UPDATE tasks SET state=?, fence=fence+1, lease_owner=NULL, updated=? "
                               "WHERE mission_id=? AND task_id=?", (TaskState.QUEUED.value, _now(),
                                                                     t["mission_id"], t["task_id"]))
                    self._event(db, t["mission_id"], "recovery", "TASK_REQUEUED",
                                {"task_id": t["task_id"], "reapply_persisted_proposal": t["proposal"] is not None})
                    requeued.append(t["task_id"])
            charged = db.execute("UPDATE spend SET actual_usd=reserved_usd, measured=0, settled=? "
                                 "WHERE settled IS NULL", (_now(),)).rowcount
            db.execute("UPDATE missions SET state=?, updated=? WHERE state=?",
                       (MissionState.RUNNING.value, _now(), MissionState.VERIFYING.value))
            db.execute("COMMIT")
        return {"requeued": requeued, "blocked": blocked, "unsettled_calls_charged": charged}

    # ================================================================ execution
    def _claim(self, owner: str, mission_id: str):
        """Atomically lease the next ready task; returns (task row, fence) or None."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            mission = self._mission(owner, mission_id, db)
            state = MissionState(mission["state"])
            if state not in (MissionState.QUEUED, MissionState.RUNNING):
                db.execute("ROLLBACK")
                raise MissionConflict(f"Mission is {state.value}; it cannot run")
            tasks = db.execute("SELECT * FROM tasks WHERE mission_id=? ORDER BY seq", (mission_id,)).fetchall()
            if any(t["state"] in (TaskState.RUNNING.value, TaskState.BLOCKED.value, TaskState.FAILED.value)
                   for t in tasks):
                db.execute("ROLLBACK")
                raise MissionConflict("A task is running, blocked or failed; reconcile first")
            done = {t["task_id"] for t in tasks if t["state"] == TaskState.COMPLETED.value}
            ready = next((t for t in tasks if t["state"] == TaskState.QUEUED.value
                          and set(json.loads(t["depends_on"])) <= done), None)
            if ready is None:
                db.execute("COMMIT")
                return None
            fence = ready["fence"] + 1
            expires = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()
            db.execute("UPDATE tasks SET state=?, lease_owner=?, lease_expires=?, fence=?, attempts=attempts+?, "
                       "updated=? WHERE mission_id=? AND task_id=?",
                       (TaskState.RUNNING.value, self.worker_id, expires, fence,
                        0 if ready["proposal"] else 1, _now(), mission_id, ready["task_id"]))
            if state == MissionState.QUEUED:
                self._set_state(db, mission_id, MissionState.RUNNING, self.worker_id)
            db.execute("COMMIT")
            return dict(ready), fence

    def _finish(self, mission_id: str, task_id: str, fence: int, state: TaskState, **fields) -> bool:
        """Fenced completion: a cancelled or re-leased task cannot be completed late."""
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            m_state = db.execute("SELECT state FROM missions WHERE id=?", (mission_id,)).fetchone()[0]
            changed = 0
            if m_state in (MissionState.RUNNING.value,):
                changed = db.execute(
                    f"UPDATE tasks SET state=?, lease_owner=NULL, updated=?{', ' + cols if cols else ''} "
                    "WHERE mission_id=? AND task_id=? AND fence=? AND state=?",
                    (state.value, _now(), *fields.values(), mission_id, task_id, fence,
                     TaskState.RUNNING.value)).rowcount
            if changed:
                self._event(db, mission_id, self.worker_id, "TASK_" + state.value, {"task_id": task_id})
            db.execute("COMMIT")
        return bool(changed)

    def _persist_proposal(self, mission_id: str, task_id: str, fence: int, proposal: Dict[str, Any]) -> bool:
        with self._db() as db:
            return db.execute("UPDATE tasks SET proposal=? WHERE mission_id=? AND task_id=? AND fence=? AND state=?",
                              (json.dumps(proposal), mission_id, task_id, fence, TaskState.RUNNING.value)).rowcount == 1

    def step(self, owner: str, mission_id: str) -> Dict[str, Any]:
        """Run exactly one unit of work (a task, or verification) and return status."""
        self.stop_latch.check()
        mission = self._mission(owner, mission_id)
        if MissionState(mission["state"]) == MissionState.VERIFYING:
            return self._verify(owner, mission_id)
        claimed = self._claim(owner, mission_id)
        if claimed is None:
            with self._db() as db:
                pending = db.execute("SELECT COUNT(*) FROM tasks WHERE mission_id=? AND state NOT IN (?,?)",
                                     (mission_id, TaskState.COMPLETED.value, TaskState.CANCELLED.value)).fetchone()[0]
                if pending:
                    raise MissionConflict("No task is ready (unsatisfied dependencies)")
                self._set_state(db, mission_id, MissionState.VERIFYING, self.worker_id)
            return self._verify(owner, mission_id)
        task_row, fence = claimed
        task = PlannedTask(id=task_row["task_id"], role=AgentRole(task_row["role"]), title=task_row["title"],
                           instructions=task_row["instructions"], depends_on=json.loads(task_row["depends_on"]))
        ws = self._workspace(mission_id)
        try:
            if task_row["proposal"]:
                output = json.loads(task_row["proposal"])  # recovery: re-apply, no new model call
            else:
                plan = json.loads(mission["plan"]) if mission["plan"] else {}
                parsed, response = run_specialist(
                    task, mission["objective"], ws.read_files(),
                    lambda req: self._call_model(mission, task.id, req), mission_id=mission_id,
                    failure=task_row["failure_context"], interface_contract=plan.get("interface_contract", ""))
                output = {"schema": "review" if task.role == AgentRole.REVIEWER else "work",
                          "data": parsed.model_dump(mode="json"),
                          "provider": getattr(response.provider, "value", str(response.provider)),
                          "model": response.model_name, "simulated": response.is_mock}
                if not self._persist_proposal(mission_id, task.id, fence, output):
                    return self.status(owner, mission_id)  # cancelled or re-leased meanwhile
            if task.role == AgentRole.REVIEWER:
                report = ReviewReport.model_validate(output["data"])
                result = {"findings": [f.model_dump() for f in report.findings], "notes": report.notes}
                with self._db() as db:
                    self._receipt(db, mission_id, task.id, "REVIEW", {
                        "findings": len(report.findings), "provider": output["provider"], "model": output["model"],
                        "simulated": output["simulated"], "advisory_only": True})
            else:
                work = AgentWorkProduct.model_validate(output["data"])
                writes = ws.apply(task.role, work.files)
                result = {"files": writes, "notes": work.notes, "uncertainty": work.uncertainty}
                with self._db() as db:
                    self._receipt(db, mission_id, task.id, "FILE_WRITES", {
                        "role": task.role.value, "files": writes, "provider": output["provider"],
                        "model": output["model"], "simulated": output["simulated"],
                        "args_sha256": hashlib.sha256(_canonical(output["data"])).hexdigest()})
            self._finish(mission_id, task.id, fence, TaskState.COMPLETED, result=json.dumps(result))
        except EmergencyStopActive:
            raise
        except AgentOutputRejected as exc:
            if task_row["attempts"] + 1 < MAX_TASK_ATTEMPTS:
                self._retry(mission_id, task.id, fence, str(exc))
            else:
                self._fail_task(mission_id, task.id, fence, f"{type(exc).__name__}: {exc}")
        except (SandboxViolation, PlanRejected, ValueError) as exc:
            self._fail_task(mission_id, task.id, fence, f"{type(exc).__name__}: {exc}")
        except (MissionBudgetExceeded, BudgetExceededError, SandboxUnavailable) as exc:
            self._block(mission_id, task.id, fence, str(exc))
        except Exception as exc:  # provider outage, etc.: block, never substitute output
            self._block(mission_id, task.id, fence, f"{type(exc).__name__}: {str(exc)[:300]}")
        return self.status(owner, mission_id)

    def _retry(self, mission_id, task_id, fence, error):
        """Malformed model output: discard it and queue one fresh attempt."""
        with self._db() as db:
            db.execute("UPDATE tasks SET state=?, proposal=NULL, lease_owner=NULL, error=?, updated=? "
                       "WHERE mission_id=? AND task_id=? AND fence=? AND state=?",
                       (TaskState.QUEUED.value, error[:1000], _now(), mission_id, task_id, fence,
                        TaskState.RUNNING.value))
            self._event(db, mission_id, self.worker_id, "TASK_RETRY", {"task_id": task_id, "error": error[:300]})

    def _fail_task(self, mission_id, task_id, fence, error):
        if self._finish(mission_id, task_id, fence, TaskState.FAILED, error=error[:1000]):
            with self._db() as db:
                self._set_state(db, mission_id, MissionState.FAILED, self.worker_id,
                                error=f"Task {task_id} failed: {error[:500]}")

    def _block(self, mission_id, task_id, fence, error):
        if self._finish(mission_id, task_id, fence, TaskState.BLOCKED, error=error[:1000]):
            with self._db() as db:
                self._set_state(db, mission_id, MissionState.BLOCKED, self.worker_id,
                                error=f"Task {task_id} blocked: {error[:500]}")

    def _verify(self, owner: str, mission_id: str) -> Dict[str, Any]:
        ws = self._workspace(mission_id)
        try:
            decision = verify(ws)
        except SandboxUnavailable as exc:
            decision = VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=[],
                                            workspace_sha256=ws.digest(), reason=str(exc))
        if self.stop_latch.engaged:
            # Processes may have been killed by the stop: that is not evidence of failure.
            with self._db() as db:
                self._set_state(db, mission_id, MissionState.BLOCKED, "verifier",
                                error="Emergency stop during verification; result discarded")
            return self.status(owner, mission_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            mission = self._mission(owner, mission_id, db)
            if mission["state"] != MissionState.VERIFYING.value:
                db.execute("ROLLBACK")  # cancelled while verifying: discard the result
                return self.status(owner, mission_id)
            self._receipt(db, mission_id, None, "VERIFICATION", {
                "verdict": decision.verdict.value, "workspace_sha256": decision.workspace_sha256,
                "checks": [{"name": c.name, "exit_code": c.exit_code, "passed": c.passed,
                            "tests_collected": c.tests_collected, "suite_invalid": c.suite_invalid}
                           for c in decision.checks],
                "network_isolated": ws.network_isolated, "verifier": "deterministic-process-checks"})
            if decision.verdict == VerificationVerdict.PASS:
                db.execute("COMMIT")
                return self._package(owner, mission_id, decision)
            if decision.verdict == VerificationVerdict.FAIL and mission["repairs"] < MAX_REPAIRS:
                n = mission["repairs"] + 1
                broken_qa = any(c.suite_invalid for c in decision.checks if c.name == "independent_acceptance_tests")
                failing = [c for c in decision.checks if not c.passed]
                report = "\n\n".join(f"[{c.name}] exit={c.exit_code}\n{c.output_tail[-3000:]}" for c in failing)
                if broken_qa:
                    # The acceptance suite cannot even be collected: the QA agent fixes its own tests.
                    role, repair_id, title = AgentRole.QA, f"qa_repair_{n}", "Repair broken acceptance tests"
                    instructions = ("Your acceptance tests could not be collected. Fix them so they run, keep testing the "
                                    "objective through the interface contract only, and do not weaken assertions.")
                else:
                    role, repair_id, title = AgentRole.ENGINEER, f"repair_{n}", "Repair failing checks"
                    instructions = ("Fix the application so that the failing independent checks pass. "
                                    "Do not modify or delete tests to make them pass.")
                seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM tasks WHERE mission_id=?", (mission_id,)).fetchone()[0]
                db.execute("INSERT INTO tasks (mission_id, task_id, seq, role, title, instructions, depends_on, state, "
                           "failure_context, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (mission_id, repair_id, seq, role.value, title, instructions, "[]", TaskState.QUEUED.value,
                            report, _now()))
                self._set_state(db, mission_id, MissionState.RUNNING, "verifier", repairs=n,
                                verdict=decision.verdict.value, error=decision.reason)
            else:
                final = MissionState.FAILED if decision.verdict == VerificationVerdict.FAIL else MissionState.UNVERIFIED
                self._set_state(db, mission_id, final, "verifier", verdict=decision.verdict.value,
                                error=decision.reason)
            db.execute("COMMIT")
        return self.status(owner, mission_id)

    def _package(self, owner: str, mission_id: str, decision: VerificationDecision) -> Dict[str, Any]:
        ws = self._workspace(mission_id)
        if ws.digest() != decision.workspace_sha256:
            with self._db() as db:
                self._set_state(db, mission_id, MissionState.UNVERIFIED, "packager",
                                error="Workspace changed between verification and packaging")
            return self.status(owner, mission_id)
        files = {rel: (ws.root / rel).read_bytes() for rel in ws.listing(limit=10_000)}
        mission = self._mission(owner, mission_id)
        manifest = {"schema_version": SCHEMA_VERSION, "mission_id": mission_id, "plan_sha256": mission["plan_sha256"],
                    "provider_mode": mission["provider_mode"], "workspace_sha256": decision.workspace_sha256,
                    "verification": decision.model_dump(mode="json"),
                    "files": {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, data in sorted(files.items()):
                info = zipfile.ZipInfo(rel, date_time=(2026, 1, 1, 0, 0, 0))
                zf.writestr(info, data)
            zf.writestr(zipfile.ZipInfo("HOOD_MANIFEST.json", date_time=(2026, 1, 1, 0, 0, 0)),
                        json.dumps(manifest, indent=2, sort_keys=True))
        data = buf.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / "artifacts" / f"{mission_id}.zip"
        with open(path, "xb") as fh:  # write-once
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(path, 0o600)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                       ("art_" + uuid.uuid4().hex, mission_id, owner, f"hood-{mission_id}.zip", path.name, digest,
                        len(data), "application/zip", decision.workspace_sha256, _now()))
            self._receipt(db, mission_id, None, "ARTIFACT", {"sha256": digest, "bytes": len(data),
                                                             "workspace_sha256": decision.workspace_sha256})
            self._set_state(db, mission_id, MissionState.COMPLETED, "packager", verdict="PASS", error=None)
            db.execute("COMMIT")
        return self.status(owner, mission_id)

    def run(self, owner: str, mission_id: str, max_steps: int = 50) -> Dict[str, Any]:
        """Step until the mission is terminal, blocked or awaiting a human."""
        status = self.status(owner, mission_id)
        for _ in range(max_steps):
            if status["state"] not in (MissionState.QUEUED.value, MissionState.RUNNING.value,
                                       MissionState.VERIFYING.value):
                break
            status = self.step(owner, mission_id)
        return status

    # ================================================================ read models
    def status(self, owner: str, mission_id: str) -> Dict[str, Any]:
        mission = self._mission(owner, mission_id)
        with self._db() as db:
            tasks = [dict(r) for r in db.execute(
                "SELECT task_id, role, title, depends_on, state, attempts, error, result FROM tasks "
                "WHERE mission_id=? ORDER BY seq", (mission_id,))]
            artifact = db.execute("SELECT name, sha256, bytes, mime, created FROM artifacts WHERE mission_id=?",
                                  (mission_id,)).fetchone()
            verification = db.execute("SELECT body FROM receipts WHERE mission_id=? AND kind='VERIFICATION' "
                                      "ORDER BY created DESC LIMIT 1", (mission_id,)).fetchone()
        for t in tasks:
            t["depends_on"] = json.loads(t["depends_on"])
            t["result"] = json.loads(t["result"]) if t["result"] else None
        plan = json.loads(mission["plan"]) if mission["plan"] else None
        return {
            "mission_id": mission_id, "state": mission["state"], "objective": mission["objective"],
            "plan_sha256": mission["plan_sha256"], "plan": plan, "budget_usd": mission["budget_usd"],
            "approved_by": mission["approved_by"], "repairs": mission["repairs"],
            "provider_mode": mission["provider_mode"], "verdict": mission["verdict"], "error": mission["error"],
            "tasks": tasks, "artifact": dict(artifact) if artifact else None,
            "last_verification": json.loads(verification["body"]) if verification else None,
            "objective_verified": mission["state"] == MissionState.COMPLETED.value,
            "simulated": mission["provider_mode"] in ("SIMULATED", "MIXED"),
            "spend": self.spend(owner, mission_id)["total_usd"],
        }

    def list(self, owner: str) -> List[Dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT id, state, objective, provider_mode, verdict, created, updated FROM missions "
                "WHERE owner=? ORDER BY created DESC LIMIT 100", (owner,))]

    def events(self, owner: str, mission_id: str) -> List[Dict[str, Any]]:
        self._mission(owner, mission_id)
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT ts, actor, kind, detail FROM events WHERE mission_id=? "
                                                "ORDER BY seq", (mission_id,))]

    def artifact(self, owner: str, mission_id: str) -> tuple[str, bytes, str]:
        """Return (filename, bytes, sha256) after re-hashing and checking receipts."""
        self._mission(owner, mission_id)
        with self._db() as db:
            row = db.execute("SELECT * FROM artifacts WHERE mission_id=? AND owner=?", (mission_id, owner)).fetchone()
        if not row:
            raise MissionConflict("No verified artifact for this mission")
        if not self.verify_receipts(owner, mission_id)["valid"]:
            raise MissionConflict("Mission receipts failed integrity check; artifact withheld")
        path = self.root / "artifacts" / row["path"]
        if path.is_symlink() or not path.is_file():
            raise MissionConflict("Artifact file missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise MissionConflict("Artifact content does not match its recorded hash")
        return row["name"], data, row["sha256"]
