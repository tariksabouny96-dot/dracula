"""Export service: validate a spec, render requested formats, independently
validate each output, and register only the outputs that pass.

A format that fails to render or validate is reported with its reason and is
never stored. The whole operation checks the emergency-stop latch before it
writes anything.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from packages.security import StopLatch

from services.artifacts.registry import ArtifactRegistry

from . import renderers as R
from . import validators as V
from .spec import DocumentSpec, FORMATS


class ExportService:
    def __init__(self, data_dir: Path, registry: Optional[ArtifactRegistry] = None,
                 stop_latch: Optional[StopLatch] = None):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "exports.db"
        self.registry = registry or ArtifactRegistry(self.dir.parent / "artifacts")
        self.stop_latch = stop_latch or StopLatch()
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS exports(
                exp_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                created_at REAL NOT NULL, status TEXT NOT NULL, outputs TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exp_owner ON exports(owner)")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _render(self, fmt: str, spec: DocumentSpec, rendered: Dict[str, bytes]) -> bytes:
        if fmt == "md":
            return R.render_md(spec)
        if fmt == "html":
            return R.render_html(spec)
        if fmt == "csv":
            return R.render_csv(spec)
        if fmt == "xlsx":
            return R.render_xlsx(spec)
        if fmt == "docx":
            return R.render_docx(spec)
        if fmt == "pdf":
            data, _notes = R.render_pdf(spec)
            return data
        if fmt == "zip":
            members = {f"document.{R.FORMAT_MEDIA[f][1]}": b for f, b in rendered.items() if f != "zip"}
            if not members:
                raise ValueError("zip requires at least one other successfully rendered format")
            return R.render_zip(members)
        raise ValueError(f"unknown format {fmt}")

    def export(self, owner: str, spec_data: dict, formats: List[str]) -> Dict[str, Any]:
        self.stop_latch.check()
        spec = DocumentSpec.parse(spec_data)
        requested = [f for f in formats if f in FORMATS]
        if not requested:
            raise ValueError(f"no valid formats requested (choose from {sorted(FORMATS)})")
        # zip is rendered last, from the other successful members.
        ordered = [f for f in requested if f != "zip"] + ([f for f in requested if f == "zip"])

        exp_id = "exp_" + os.urandom(16).hex()
        rendered: Dict[str, bytes] = {}
        outputs: List[Dict[str, Any]] = []

        for fmt in ordered:
            try:
                data = self._render(fmt, spec, rendered)
                ok, why = V.validate(fmt, data)
                if not ok:
                    outputs.append({"format": fmt, "status": "failed", "reason": why})
                    continue
                self.stop_latch.check()
                mime, ext = R.FORMAT_MEDIA[fmt]
                name = f"{_slug(spec.title)}.{ext}"
                art = self.registry.store(owner, name, mime, data)
                rendered[fmt] = data
                outputs.append({"format": fmt, "status": "ok", "artifact_id": art["artifact_id"],
                                "name": name, "sha256": art["sha256"], "bytes": art["bytes"]})
            except Exception as exc:
                outputs.append({"format": fmt, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"})

        ok_count = sum(1 for o in outputs if o["status"] == "ok")
        status = "complete" if ok_count == len(ordered) else ("partial" if ok_count else "failed")
        record = {"export_id": exp_id, "title": spec.title, "status": status,
                  "created_at": time.time(), "outputs": outputs}
        import json
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO exports VALUES (?,?,?,?,?,?)",
                         (exp_id, owner, spec.title, record["created_at"], status, json.dumps(outputs)))
            conn.commit()
        return record

    def list(self, owner: str) -> List[Dict[str, Any]]:
        import json
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT exp_id,title,created_at,status,outputs FROM exports WHERE owner=? ORDER BY created_at DESC",
                (owner,)).fetchall()
        return [{"export_id": r["exp_id"], "title": r["title"], "created_at": r["created_at"],
                 "status": r["status"], "outputs": json.loads(r["outputs"])} for r in rows]

    def get(self, owner: str, exp_id: str) -> Dict[str, Any]:
        import json
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM exports WHERE exp_id=? AND owner=?", (exp_id, owner)).fetchone()
        if not row:
            raise KeyError(exp_id)
        return {"export_id": row["exp_id"], "title": row["title"], "created_at": row["created_at"],
                "status": row["status"], "outputs": json.loads(row["outputs"])}

    def self_test(self) -> Dict[str, Any]:
        """Render+validate a tiny spec in every format; report which pass."""
        spec = DocumentSpec.parse({"title": "self-test", "blocks": [
            {"type": "heading", "level": 1, "text": "ok"},
            {"type": "table", "columns": ["a", "b"], "rows": [["1", "2"]]}]})
        results, rendered = {}, {}
        for fmt in [f for f in FORMATS if f != "zip"] + ["zip"]:
            try:
                data = self._render(fmt, spec, rendered)
                ok, why = V.validate(fmt, data)
                results[fmt] = "ok" if ok else f"invalid: {why}"
                if ok:
                    rendered[fmt] = data
            except Exception as exc:
                results[fmt] = f"error: {type(exc).__name__}"
        return results


def _slug(title: str) -> str:
    keep = "".join(c if (c.isalnum() or c in "-_") else "-" for c in title.strip().lower())
    out = "-".join(p for p in keep.split("-") if p) or "document"
    return out[:60]
