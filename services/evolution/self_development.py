"""Governed self-development.

HOOD can inspect, propose, and test improvements to its own code — but it can
never change itself on its own authority. Every self-change goes through:

    propose  ->  (owner reviews)  ->  approve  ->  apply (with rollback)

Hard guarantees that keep the owner the final authority:
- A proposal is only ever *recorded*; it is never written to the live tree.
- Applying requires the Root Owner AND an ApprovalService approval bound to the
  exact content hash of that proposal (an approval for one change can't apply a
  different one).
- A constitutional set of files — authentication, approvals, the firewall, the
  emergency stop, this controller itself, the release gate, audit, CI — can
  NEVER be proposed or applied. HOOD cannot edit away its own guardrails.
- Apply checkpoints the file first, validates after writing, and rolls back on
  any failure. An engaged emergency stop blocks both propose and apply.
"""
from __future__ import annotations

import hashlib
import json
import os
import py_compile
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from packages.security import StopLatch, confine_path, PathConfinementError
from services.dev_executor.code_modifier import CodeModifier
from services.policy.approval_service import ApprovalService

# Files HOOD may never modify itself — its guardrails and the authority chain.
PROTECTED_PREFIXES = (
    "services/auth/",
    "services/policy/approval_service.py",
    "services/policy/governance.py",
    "services/firewall/",
    "services/core/emergency_stop.py",
    "packages/security/",
    "services/evolution/self_development.py",
    "scripts/preproduction_gate.py",
    "audit/",
    ".github/",
    "services/x_control/",
)
# Roots HOOD may propose changes within (a protected prefix still wins).
MODIFIABLE_ROOTS = ("services/", "packages/", "ui/", "scripts/", "tests/", "docs/")

MAX_CONTENT_BYTES = 1024 * 1024


class SelfDevelopmentError(Exception):
    pass


class ConstitutionalViolation(PermissionError):
    """Raised when a change targets a protected guardrail file."""


def _rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def is_protected(target_path: str) -> bool:
    rel = _rel(target_path)
    if not any(rel.startswith(root) for root in MODIFIABLE_ROOTS):
        return True  # outside the modifiable roots -> treat as protected
    return any(rel.startswith(p) for p in PROTECTED_PREFIXES)


class SelfDevelopmentController:
    def __init__(self, workspace_root: Path, approval_service: Optional[ApprovalService] = None,
                 audit_service: Any = None, data_dir: Optional[Path] = None,
                 stop_latch: Optional[StopLatch] = None, validate_command: Optional[List[str]] = None):
        self.workspace_root = Path(workspace_root).resolve()
        self.approval_service = approval_service or ApprovalService()
        self.audit_service = audit_service
        self.stop_latch = stop_latch or StopLatch()
        self.code_modifier = CodeModifier(self.workspace_root)
        self.validate_command = validate_command  # optional extra gate, e.g. ["pytest","-q","tests/x"]
        base = Path(data_dir) if data_dir else Path(
            os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "self_dev"
        base.mkdir(parents=True, exist_ok=True)
        self.dir = base
        self.proposals_dir = base / "proposals"
        self.proposals_dir.mkdir(exist_ok=True)
        self.db_path = base / "self_dev.db"
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS proposals(
                id TEXT PRIMARY KEY, target_path TEXT NOT NULL, rationale TEXT NOT NULL,
                sha256 TEXT NOT NULL, proposed_by TEXT NOT NULL, created_at REAL NOT NULL,
                state TEXT NOT NULL, approval_id TEXT, applied_at REAL, detail TEXT)""")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _audit(self, action, actor, detail, decision="ALLOW"):
        if self.audit_service is None:
            return
        try:
            from packages.contracts import AuditEvent
            self.audit_service.record_event(AuditEvent(
                actor=actor, action=action, target="SELF_DEVELOPMENT",
                policy_decision=decision, result=detail[:500], verification="PASSED"))
        except Exception:
            pass

    # ---------------------------------------------------------------- propose
    def propose(self, target_path: str, new_content: str, rationale: str,
                proposed_by: str = "hood") -> Dict[str, Any]:
        self.stop_latch.check()
        rel = _rel(target_path)
        if is_protected(rel):
            self._audit("SELF_DEV_PROPOSAL_REFUSED", proposed_by, f"protected target {rel}", decision="DENY")
            raise ConstitutionalViolation(
                f"{rel} is a protected guardrail; HOOD may never modify it. Owner edits it directly.")
        try:
            confine_path(self.workspace_root, rel, label="self-dev target")
        except PathConfinementError as exc:
            raise SelfDevelopmentError(str(exc)) from None
        if not isinstance(new_content, str) or len(new_content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise SelfDevelopmentError("content missing or too large")
        if rel.endswith(".py"):
            # Must at least be syntactically valid Python before an owner reviews it.
            try:
                compile(new_content, rel, "exec")
            except SyntaxError as exc:
                raise SelfDevelopmentError(f"proposed Python is not syntactically valid: {exc}") from None

        digest = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
        pid = "sd_" + os.urandom(12).hex()
        (self.proposals_dir / pid).write_text(new_content, encoding="utf-8")

        req = self.approval_service.create_request(
            task_id=pid,
            action_type="SELF_MODIFY",
            target=rel,
            reason=f"HOOD self-development: {rationale}"[:500],
            options=[{"label": "APPROVE_SELF_MODIFY", "parameter_sha256": digest, "target": rel}],
            recommended_option="APPROVE_SELF_MODIFY",
            principal="Zak",
        )
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (pid, rel, rationale, digest, proposed_by, time.time(),
                          "AWAITING_APPROVAL", req.approval_id, None, None))
            conn.commit()
        self._audit("SELF_DEV_PROPOSED", proposed_by, f"{rel} sha={digest[:12]} approval={req.approval_id}")
        return {"proposal_id": pid, "target_path": rel, "sha256": digest,
                "state": "AWAITING_APPROVAL", "approval_id": req.approval_id, "rationale": rationale}

    # ------------------------------------------------------------------ apply
    def apply(self, proposal_id: str, approver: str = "Zak", is_root_owner: bool = False) -> Dict[str, Any]:
        self.stop_latch.check()
        if not is_root_owner:
            raise PermissionError("Only the Root Owner may apply a self-development change.")
        row = self._row(proposal_id)
        if row["state"] != "AWAITING_APPROVAL":
            raise SelfDevelopmentError(f"proposal is {row['state']}, not AWAITING_APPROVAL")
        rel, digest = row["target_path"], row["sha256"]
        if is_protected(rel):  # re-check at apply time
            raise ConstitutionalViolation(f"{rel} is protected; refusing to apply.")

        # The approval must be APPROVED and bound to this exact content hash.
        req = self.approval_service.get_request(row["approval_id"])
        if not req or not self.approval_service.is_approved(row["approval_id"]):
            raise PermissionError("No owner approval for this proposal; refusing to apply.")
        opt_hash = (req.options[0].get("parameter_sha256") if req.options else None)
        if opt_hash != digest:
            raise PermissionError("Approval is not bound to this proposal's content; refusing.")

        content = (self.proposals_dir / proposal_id).read_text(encoding="utf-8")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            raise SelfDevelopmentError("stored proposal content no longer matches its hash")

        edit = self.code_modifier.apply_targeted_edit(rel, content)
        checkpoint = edit.checkpoint_ref
        ok, detail = self._validate(rel)
        if not ok:
            # Fail closed: undo the change.
            try:
                self.code_modifier.restore_file_checkpoint(rel, checkpoint)
            except Exception as exc:  # pragma: no cover - restore should succeed
                detail += f"; ROLLBACK FAILED: {exc}"
            self._finish(proposal_id, "ROLLED_BACK", detail)
            self._audit("SELF_DEV_ROLLED_BACK", approver, f"{rel}: {detail}", decision="DENY")
            return {"proposal_id": proposal_id, "state": "ROLLED_BACK", "detail": detail,
                    "checkpoint_ref": checkpoint}
        self._finish(proposal_id, "APPLIED", detail)
        self._audit("SELF_DEV_APPLIED", approver, f"{rel} sha={digest[:12]}")
        return {"proposal_id": proposal_id, "state": "APPLIED", "detail": detail,
                "checkpoint_ref": checkpoint, "target_path": rel}

    def _validate(self, rel: str) -> (bool, str):
        target = self.workspace_root / rel
        if rel.endswith(".py"):
            try:
                py_compile.compile(str(target), doraise=True)
            except py_compile.PyCompileError as exc:
                return False, f"py_compile failed: {exc}"
        if self.validate_command:
            try:
                proc = subprocess.run(self.validate_command, cwd=str(self.workspace_root),
                                      capture_output=True, timeout=600, text=True)
            except (subprocess.SubprocessError, OSError) as exc:
                return False, f"validation command error: {exc}"
            if proc.returncode != 0:
                return False, f"validation command failed (exit {proc.returncode}): {proc.stdout[-500:]}"
        return True, "validated"

    def _finish(self, pid, state, detail):
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE proposals SET state=?, applied_at=?, detail=? WHERE id=?",
                         (state, time.time(), detail[:1000], pid))
            conn.commit()

    def _row(self, pid: str) -> sqlite3.Row:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not row:
            raise KeyError(pid)
        return row

    def list(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM proposals ORDER BY created_at DESC").fetchall()
        return [self._view(r) for r in rows]

    def get(self, pid: str) -> Dict[str, Any]:
        return self._view(self._row(pid))

    @staticmethod
    def _view(r) -> Dict[str, Any]:
        return {"proposal_id": r["id"], "target_path": r["target_path"], "rationale": r["rationale"],
                "sha256": r["sha256"], "proposed_by": r["proposed_by"], "created_at": r["created_at"],
                "state": r["state"], "approval_id": r["approval_id"], "applied_at": r["applied_at"],
                "detail": r["detail"]}
