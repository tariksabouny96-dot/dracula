"""Self-repair: the owner reports a problem with HOOD; HOOD investigates its own code, proposes a
minimal fix plus a regression test, proves both in the sandbox, and changes its code only when the
Root Owner approves that exact fix (restore point, one-click undo).

Owner's rules (2026-10-10):
- HOOD says it couldn't find a reliable fix rather than guess: a "can't fix", a low-confidence
  answer, an invalid proposal, or a fix that fails its checks after one repair round all end as
  NO_RELIABLE_FIX with the diagnosis, and nothing changes.
- Nothing is applied without the Root Owner's approval of that exact proposal (hash-bound).
- Guardrails are never edited by HOOD: the self-development protected list (auth, approvals,
  firewall, emergency stop, X control, security primitives...) plus the sandbox, the self-repair
  service itself, the HTTP security boundary, deployment files and existing tests.

The proof, in HOOD's sandbox (no network, limits): the new regression test FAILS on today's code
(it reproduces the problem), PASSES with the fix, and HOOD's whole test suite still passes.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from packages.contracts import ModelClass, ModelRequest
from packages.logging.redactor import redact_string
from services.evolution.self_development import is_protected

REPO_ROOT = Path(__file__).resolve().parents[2]

# Never edited by self-repair, on top of services/evolution/self_development.PROTECTED_PREFIXES.
EXTRA_PROTECTED = ("services/selfrepair/", "services/agents/sandbox.py", "services/agents/netns_launcher.py",
                   "services/toolbox/wsl.py", "services/toolbox/catalog.py", "scripts/wsl/", "ui/server.py",
                   "ui/routes.py", "packages/config/paths.py", "scripts/hood_backup.py", "Dockerfile",
                   "docker-compose.yml", "deploy/", "tests/")
EDITABLE_SUFFIXES = (".py", ".js", ".css", ".html", ".md", ".json", ".yaml", ".yml")
CONTEXT_SUFFIXES = (".py", ".js", ".css", ".html")
CONTEXT_ROOTS = ("ui/", "services/", "packages/", "scripts/", "hood_cli.py")
MAX_EDITS, MAX_FILES, MAX_SNIPPET = 8, 4, 20_000
MAX_CONTEXT_CHARS, MAX_FILE_EXCERPT = 110_000, 26_000
MAX_SCREENSHOT_BYTES = 4 * 1024 * 1024
IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
TEST_PATH_RE = re.compile(r"^tests/selfrepair/test_[a-z0-9_]{3,60}\.py$")
SUITE_ARGS = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
              "-m", "not browser_e2e and not live_provider and not live_network", "tests"]
STOPWORDS = set("""that this with from have when what where which there their they them then than your yours
about after again also because been before being both does doing down each just like more most only other
over same some such very will would could should into onto upon here hood it's dont doesn't isn't can't
please thanks thank page button click clicked shows show showing works working work broken error problem
issue still always never anymore nothing something everything""".split())

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "understood_problem": {"type": "string"},
        "root_cause": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "can_fix": {"type": "boolean"},
        "why_not": {"type": "string"},
        "edits": {"type": "array", "items": {"type": "object", "properties": {
            "path": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"}},
            "required": ["path", "find", "replace"]}},
        "test_path": {"type": "string"},
        "test_content": {"type": "string"},
        "summary_for_owner": {"type": "string"},
    },
    "required": ["understood_problem", "root_cause", "confidence", "can_fix", "why_not", "edits",
                 "test_path", "test_content", "summary_for_owner"],
}

SYSTEM_PROMPT = """You are HOOD's self-repair engineer. HOOD's owner reported a problem with HOOD itself.
You get the report, a description of their screenshot (if any), recent errors HOOD recorded, and
excerpts of HOOD's own source code. Reply with JSON only, following the schema.

Rules:
1. Only propose a fix you are confident solves the reported problem. If the excerpts don't show the
   cause, the cause is in a PROTECTED file, or the problem isn't a code bug (a setting, a key, a
   quota, the network), set can_fix=false, edits=[], and explain in why_not. Never guess.
2. Keep the change minimal. Each edit replaces one exact snippet ("find") that appears EXACTLY ONCE
   in that file, copied character for character including indentation. Prefer short unique snippets.
3. Never edit tests, PROTECTED files, security checks, or files that were not shown to you.
4. Write ONE new pytest file at tests/selfrepair/test_<short_name>.py that FAILS on the current code
   and PASSES after your edits. Test behaviour where possible; for JavaScript/CSS/HTML read the file
   (paths relative to the repository root: Path(__file__).resolve().parents[2]) and assert the
   corrected content. No network, no sleeps, no external services.
5. confidence: "high" only if you can point to the exact faulty lines; "low" means don't fix.
6. summary_for_owner: 2-4 plain sentences for a non-programmer: what was wrong and what changes."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def protected(rel: str) -> bool:
    rel = rel.replace("\\", "/").lstrip("./")
    return is_protected(rel) or any(rel == p or rel.startswith(p) for p in EXTRA_PROTECTED)


class SelfRepairError(RuntimeError):
    pass


class SelfRepairConflict(SelfRepairError):
    pass


class SelfRepairService:
    def __init__(self, data_dir: Path, router: Any = None, *, repo_root: Path = REPO_ROOT,
                 invoke: Optional[Callable[[ModelRequest], Any]] = None, stop_latch: Any = None,
                 suite_args: Optional[List[str]] = None, local_run_allowed: Optional[Callable[[], bool]] = None,
                 recent_errors: Optional[Callable[[], List[str]]] = None, synchronous: bool = False):
        self.root = Path(data_dir) / "self_repair"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "self_repair.sqlite3"
        self.repo = Path(repo_root).resolve()
        self.router = router
        self._invoke = invoke or (router.invoke if router is not None else None)
        self.stop_latch = stop_latch
        self.suite_args = list(suite_args or SUITE_ARGS)
        self.local_run_allowed = local_run_allowed or (lambda: False)
        self.recent_errors = recent_errors or (lambda: [])
        self.synchronous = synchronous            # tests: run the investigation inline
        self.restart_hook: Optional[Callable[[], None]] = None
        self._busy = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------ storage
    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self) -> None:
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                state TEXT NOT NULL, report TEXT NOT NULL, screenshot TEXT, screenshot_note TEXT,
                diagnosis TEXT, proposal TEXT, proposal_sha TEXT, diff TEXT, checks TEXT, applied TEXT,
                error TEXT, log TEXT NOT NULL DEFAULT '[]')""")

    def _set(self, rid: str, **fields) -> None:
        enc = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in fields.items()}
        cols = ", ".join(f"{k}=?" for k in enc)
        with self._db() as db:
            db.execute(f"UPDATE reports SET {cols}, updated=? WHERE id=?", (*enc.values(), _now(), rid))

    def _log(self, rid: str, line: str) -> None:
        with self._db() as db:
            row = db.execute("SELECT log FROM reports WHERE id=?", (rid,)).fetchone()
            log = json.loads(row["log"]) if row else []
            log.append(f"{time.strftime('%H:%M:%S')} {line}")
            db.execute("UPDATE reports SET log=?, updated=? WHERE id=?", (json.dumps(log[-80:]), _now(), rid))

    def _row(self, rid: str) -> sqlite3.Row:
        with self._db() as db:
            row = db.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise KeyError(rid)
        return row

    def get(self, rid: str) -> Dict[str, Any]:
        row = self._row(rid)
        out = {k: row[k] for k in row.keys() if k not in ("screenshot",)}
        for k in ("diagnosis", "proposal", "checks", "applied", "log"):
            out[k] = json.loads(row[k]) if row[k] else None
        out["has_screenshot"] = bool(row["screenshot"])
        out["needs_restart"] = bool(out["applied"] and out["applied"].get("python_changed"))
        return out

    def list(self) -> List[Dict[str, Any]]:
        with self._db() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM reports ORDER BY created DESC LIMIT 100")]
        return [self.get(i) for i in ids]

    def screenshot(self, rid: str) -> Tuple[bytes, str]:
        row = self._row(rid)
        if not row["screenshot"]:
            raise KeyError(rid)
        meta = json.loads(row["screenshot"])
        return (self.root / rid / "screenshot.bin").read_bytes(), meta["mime_type"]

    # ------------------------------------------------------------------ owner actions
    def report(self, owner: str, text: str, screenshot_b64: Optional[str] = None,
               screenshot_mime: Optional[str] = None, screenshot_consent: bool = False) -> Dict[str, Any]:
        """Start an investigation. A screenshot is only sent to the model with the owner's consent."""
        if self.stop_latch is not None:
            self.stop_latch.check()
        text = (text or "").strip()
        if not 10 <= len(text) <= 8000:
            raise ValueError("Describe the problem in 10 to 8,000 characters")
        if self._invoke is None:
            raise SelfRepairError("No AI model is attached to HOOD")
        shot = None
        if screenshot_b64:
            if not screenshot_consent:
                raise ValueError("Confirm that the screenshot may be sent to the AI provider (it may show private data)")
            if screenshot_mime not in IMAGE_MIME:
                raise ValueError("The screenshot must be PNG, JPEG or WebP")
            try:
                raw = base64.b64decode(screenshot_b64, validate=True)
            except (ValueError, TypeError):
                raise ValueError("The screenshot isn't valid base64") from None
            if not raw or len(raw) > MAX_SCREENSHOT_BYTES:
                raise ValueError("The screenshot must be under 4 MB")
            shot = (raw, screenshot_mime)
        if not self._busy.acquire(blocking=False):
            raise SelfRepairConflict("HOOD is already investigating another problem; try again when it's done")
        rid = "sr_" + uuid.uuid4().hex[:12]
        try:
            (self.root / rid).mkdir(parents=True, exist_ok=True)
            meta = None
            if shot:
                (self.root / rid / "screenshot.bin").write_bytes(shot[0])
                meta = json.dumps({"mime_type": shot[1], "bytes": len(shot[0]), "consent": True})
            with self._db() as db:
                db.execute("INSERT INTO reports (id, owner, created, updated, state, report, screenshot) "
                           "VALUES (?,?,?,?,?,?,?)", (rid, owner, _now(), _now(), "INVESTIGATING", text, meta))
        except Exception:
            self._busy.release()
            raise
        if self.synchronous:
            self._investigate(rid)
        else:
            threading.Thread(target=self._investigate, args=(rid,), daemon=True).start()
        return self.get(rid)

    def discard(self, rid: str, actor: str) -> Dict[str, Any]:
        if self._row(rid)["state"] not in ("NEEDS_DECISION", "AWAITING_LOCAL_RUN", "NO_RELIABLE_FIX", "FAILED"):
            raise SelfRepairConflict("Only an unapplied report can be discarded")
        self._set(rid, state="DISCARDED")
        self._log(rid, f"Discarded by {actor}.")
        return self.get(rid)

    def approve_local_run(self, rid: str, actor: str) -> Dict[str, Any]:
        """No sandbox on this computer: the owner lets HOOD run the checks directly on this PC."""
        row = self._row(rid)
        if row["state"] != "AWAITING_LOCAL_RUN":
            raise SelfRepairConflict("This fix isn't waiting to have its checks run")
        if not self.local_run_allowed():
            raise SelfRepairConflict('"Run on my PC" is turned off. Turn it on in Settings > Agents first.')
        if not self._busy.acquire(blocking=False):
            raise SelfRepairConflict("HOOD is busy with another investigation")
        self._log(rid, f"{actor} approved running the checks directly on this PC.")
        self._set(rid, state="VERIFYING")
        runner = (lambda: self._verify_and_finish(rid, json.loads(row["proposal"]), unisolated=True, attempt=1))
        if self.synchronous:
            self._guarded(rid, runner)
        else:
            threading.Thread(target=self._guarded, args=(rid, runner), daemon=True).start()
        return self.get(rid)

    # ------------------------------------------------------------------ investigation
    def _guarded(self, rid: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # reported to the owner; nothing was changed
            self._set(rid, state="FAILED", error=f"{type(exc).__name__}: {str(exc)[:600]}")
            self._log(rid, "Stopped: " + str(exc)[:300])
        finally:
            if self._busy.locked():
                self._busy.release()

    def _investigate(self, rid: str) -> None:
        self._guarded(rid, lambda: self._investigate_inner(rid))

    def _investigate_inner(self, rid: str) -> None:
        row = self._row(rid)
        report = row["report"]
        note = ""
        if row["screenshot"]:
            data, mime = self.screenshot(rid)
            self._log(rid, "Reading your screenshot…")
            note = self._ask_text(rid, "Describe this screenshot of HOOD's web console for a developer: page or "
                                       "dialog shown, every visible error or warning message (verbatim), button "
                                       "labels near the problem, anything that looks wrong. Plain text, under 200 "
                                       "words.", images=[{"mime_type": mime, "data_b64": base64.b64encode(data).decode()}])
            self._set(rid, screenshot_note=note)
        self._log(rid, "Searching HOOD's code for the parts involved…")
        files = self.relevant_files(report + "\n" + note)
        if not files:
            self._finish_no_fix(rid, {"understood_problem": report[:300], "root_cause": "",
                                      "why_not": "I couldn't find any part of HOOD's code that matches this description. "
                                                 "Mention the exact text on screen (a button, a message) and try again."})
            return
        self._log(rid, "Looking at: " + ", ".join(files))
        context = self._context(files, report + "\n" + note)
        prompt = self._prompt(report, note, context)
        proposal = self._ask_fix(rid, prompt)
        self._process(rid, proposal, prompt, attempt=0)

    def _process(self, rid: str, proposal: Dict[str, Any], prompt: str, attempt: int) -> None:
        diagnosis = {k: proposal.get(k) for k in ("understood_problem", "root_cause", "confidence", "why_not",
                                                  "summary_for_owner")}
        self._set(rid, diagnosis=diagnosis)
        if not proposal.get("can_fix") or proposal.get("confidence") == "low":
            self._finish_no_fix(rid, proposal)
            return
        try:
            files = self.check_proposal(proposal)
        except SelfRepairError as exc:
            if attempt == 0:
                self._log(rid, f"The proposed fix was invalid ({exc}); asking for a corrected one…")
                again = self._ask_fix(rid, prompt + f"\n\nYour previous answer was rejected: {exc}\n"
                                                     "Return a corrected proposal, or can_fix=false.")
                self._process(rid, again, prompt, attempt=1)
                return
            self._finish_no_fix(rid, {**proposal, "why_not": f"The fix I proposed wasn't valid twice ({exc})."})
            return
        sha = self.proposal_sha(proposal)
        diff = self.unified_diff(proposal, files)
        self._set(rid, proposal=proposal, proposal_sha=sha, diff=diff, state="VERIFYING")
        self._verify_and_finish(rid, proposal, unisolated=False, attempt=attempt, prompt=prompt)

    def _verify_and_finish(self, rid: str, proposal: Dict[str, Any], *, unisolated: bool, attempt: int,
                           prompt: str = "") -> None:
        from services.agents.sandbox import sandbox_problem
        if not unisolated:
            problem = sandbox_problem() or self._sandbox_lacks_packages()
            if problem:
                self._set(rid, state="AWAITING_LOCAL_RUN",
                          error=f"The checks need a sandbox, which isn't ready here: {problem}")
                self._log(rid, "Fix ready, but its checks need HOOD's sandbox or your OK to run them on this PC.")
                return
        self._log(rid, "Proving the fix: the new test must fail on today's code, pass with the fix, and "
                       "HOOD's whole test suite must still pass…")
        checks = self.verify(rid, proposal, unisolated=unisolated)
        self._set(rid, checks=checks)
        if all(c["passed"] for c in checks):
            self._set(rid, state="NEEDS_DECISION", error=None)
            self._log(rid, "All checks passed. Waiting for your decision.")
            return
        failed = next(c for c in checks if not c["passed"])
        if attempt == 0 and prompt:
            self._log(rid, f"Check '{failed['name']}' failed; asking for one corrected fix…")
            again = self._ask_fix(rid, prompt + "\n\nYour previous fix:\n" + json.dumps(
                {k: proposal.get(k) for k in ("edits", "test_path", "test_content")})[:30000] +
                f"\n\nfailed the check '{failed['name']}' ({failed['why']}). Output:\n{failed['output'][-3000:]}\n"
                "Return a corrected proposal, or can_fix=false if you are not sure.")
            self._process(rid, again, prompt, attempt=1)
            return
        self._finish_no_fix(rid, {**proposal, "why_not": f"My fix didn't pass its checks ('{failed['name']}': "
                                                         f"{failed['why']}), so I won't propose it."})

    def _sandbox_lacks_packages(self) -> Optional[str]:
        """HOOD's tests need HOOD's own Python packages inside the sandbox (on Windows the WSL sandbox
        has only Python and pytest so far)."""
        from services.agents.sandbox import Workspace, python_cmd
        from packages.security import StopLatch
        probe = Path(tempfile.mkdtemp(prefix="hood-selfrepair-probe-"))
        try:
            r = Workspace(probe, self.stop_latch or StopLatch()).run(
                "packages", python_cmd("-c", "import pydantic, yaml, cryptography, pytest"), timeout=120)
            return None if r.exit_code == 0 else ("HOOD's sandbox doesn't have HOOD's own Python packages, "
                                                  "so HOOD's tests can't run there yet")
        except Exception as exc:  # noqa: BLE001 - any failure means: ask the owner instead
            return f"HOOD's sandbox can't run the checks: {exc}"
        finally:
            shutil.rmtree(probe, ignore_errors=True)

    def _finish_no_fix(self, rid: str, proposal: Dict[str, Any]) -> None:
        why = proposal.get("why_not") or ("My confidence in a fix is low." if proposal.get("confidence") == "low"
                                          else "No reliable fix found.")
        self._set(rid, state="NO_RELIABLE_FIX", diagnosis={
            "understood_problem": proposal.get("understood_problem"), "root_cause": proposal.get("root_cause"),
            "confidence": proposal.get("confidence"), "why_not": why}, error=None)
        self._log(rid, "I couldn't find a reliable fix: " + why[:300])

    # ------------------------------------------------------------------ model calls
    def _ask_text(self, rid: str, prompt: str, images=None) -> str:
        resp = self._invoke(ModelRequest(prompt=prompt, model_class=ModelClass.STANDARD, max_tokens=1500,
                                         temperature=0.2, task_id=f"selfrepair:{rid}", agent="self_repair",
                                         images=images or []))
        return (resp.text or "").strip()[:4000]

    def _ask_fix(self, rid: str, prompt: str) -> Dict[str, Any]:
        resp = self._invoke(ModelRequest(prompt=prompt, system_prompt=SYSTEM_PROMPT, model_class=ModelClass.DEEP,
                                         max_tokens=16000, temperature=0.1, task_id=f"selfrepair:{rid}",
                                         agent="self_repair", response_schema=FIX_SCHEMA))
        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`").split("\n", 1)[-1]
        try:
            data = json.loads(text)
        except ValueError:
            raise SelfRepairError("The AI model's answer wasn't valid JSON") from None
        if not isinstance(data, dict):
            raise SelfRepairError("The AI model's answer wasn't a JSON object")
        data.setdefault("edits", [])
        return data

    # ------------------------------------------------------------------ code context
    def tracked_files(self) -> List[str]:
        try:
            out = subprocess.run(["git", "-C", str(self.repo), "ls-files"], capture_output=True, text=True,
                                 timeout=30)
            if out.returncode == 0 and out.stdout.strip():
                return [l.strip() for l in out.stdout.splitlines() if l.strip()]
        except (OSError, subprocess.SubprocessError):
            pass
        files = []
        for root in ("ui", "services", "packages", "scripts", "tests", "config", "audit"):
            for p in (self.repo / root).rglob("*"):
                if p.is_file() and "__pycache__" not in p.parts:
                    files.append(p.relative_to(self.repo).as_posix())
        return files + [f for f in ("hood_cli.py", "pytest.ini", "requirements.txt") if (self.repo / f).is_file()]

    @staticmethod
    def keywords(text: str) -> List[str]:
        phrases = re.findall(r'["“«]([^"”»]{3,80})["”»]', text)
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{3,40}", text)
        out, seen = [], set()
        for k in [p.strip() for p in phrases] + words:
            low = k.lower()
            if low in seen or low in STOPWORDS or len(low) < 4:
                continue
            seen.add(low)
            out.append(k)
        return out[:40]

    def relevant_files(self, text: str, limit: int = 6) -> List[str]:
        keys = self.keywords(text)
        if not keys:
            return []
        phrases = [k.lower() for k in keys if " " in k]
        words = [k.lower() for k in keys if " " not in k]
        scored = []
        for rel in self.tracked_files():
            if not rel.endswith(CONTEXT_SUFFIXES) or not rel.startswith(CONTEXT_ROOTS) or rel.startswith("tests/"):
                continue
            try:
                body = (self.repo / rel).read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            score = sum(5 * body.count(p) for p in phrases)
            score += sum(min(body.count(w), 20) for w in words)
            score += sum(3 for w in words if w in rel.lower())
            hits = sum(1 for w in words if w in body) + sum(1 for p in phrases if p in body)
            if score and hits >= max(1, min(2, len(keys))):
                scored.append((score * hits, rel))
        scored.sort(reverse=True)
        return [rel for _, rel in scored[:limit]]

    def _context(self, files: List[str], text: str) -> str:
        keys = [k.lower() for k in self.keywords(text)]
        chunks, used = [], 0
        for rel in files:
            body = (self.repo / rel).read_text(encoding="utf-8", errors="replace")
            lines = body.splitlines(keepends=True)
            if len(body) <= MAX_FILE_EXCERPT:
                windows = [(0, len(lines))]
            else:
                hits = [i for i, line in enumerate(lines) if any(k in line.lower() for k in keys)]
                windows = []
                for i in hits:
                    lo, hi = max(0, i - 50), min(len(lines), i + 50)
                    if windows and lo <= windows[-1][1]:
                        windows[-1] = (windows[-1][0], hi)
                    else:
                        windows.append((lo, hi))
            tag = "PROTECTED: do not edit" if protected(rel) else "editable"
            for lo, hi in windows:
                piece = "".join(lines[lo:hi])
                if used + len(piece) > MAX_CONTEXT_CHARS:
                    break
                chunks.append(f"=== FILE {rel} ({tag}) lines {lo + 1}-{hi} of {len(lines)} ===\n{piece}")
                used += len(piece)
        return redact_string("\n".join(chunks))

    def _prompt(self, report: str, note: str, context: str) -> str:
        errors = [redact_string(str(e))[:400] for e in (self.recent_errors() or [])][:12]
        return ("OWNER'S REPORT:\n" + report.strip() + "\n\n"
                + (("SCREENSHOT (described):\n" + note + "\n\n") if note else "")
                + (("RECENT ERRORS HOOD RECORDED:\n- " + "\n- ".join(errors) + "\n\n") if errors else "")
                + "HOOD SOURCE EXCERPTS (paths are relative to the repository root):\n" + context)

    # ------------------------------------------------------------------ proposal checks
    @staticmethod
    def proposal_sha(proposal: Dict[str, Any]) -> str:
        core = {"edits": proposal.get("edits"), "test_path": proposal.get("test_path"),
                "test_content": proposal.get("test_content")}
        return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()

    def check_proposal(self, proposal: Dict[str, Any], repo: Optional[Path] = None) -> Dict[str, Tuple[str, str]]:
        """Validate a proposal against the code; returns {path: (before, after)}. Raises SelfRepairError."""
        repo = repo or self.repo
        edits = proposal.get("edits") or []
        if not edits or len(edits) > MAX_EDITS:
            raise SelfRepairError(f"a fix needs 1 to {MAX_EDITS} edits")
        files: Dict[str, Tuple[str, str]] = {}
        for edit in edits:
            rel = str(edit.get("path", "")).replace("\\", "/").strip()
            if (not rel or rel.startswith("/") or ".." in Path(rel).parts or re.match(r"^[A-Za-z]:", rel)
                    or not rel.endswith(EDITABLE_SUFFIXES)):
                raise SelfRepairError(f"not an editable path: {rel!r}")
            if protected(rel):
                raise SelfRepairError(f"{rel} is protected: HOOD never edits it itself")
            find, replace = edit.get("find"), edit.get("replace")
            if not isinstance(find, str) or not isinstance(replace, str) or not find or find == replace:
                raise SelfRepairError(f"edit for {rel} needs a non-empty 'find' different from 'replace'")
            if len(find) > MAX_SNIPPET or len(replace) > MAX_SNIPPET:
                raise SelfRepairError(f"edit for {rel} is too large")
            if rel not in files:
                target = repo / rel
                if not target.is_file() or target.is_symlink():
                    raise SelfRepairError(f"{rel} doesn't exist")
                text = target.read_text(encoding="utf-8")
                files[rel] = (text, text)
            before, current = files[rel]
            if current.count(find) != 1:
                raise SelfRepairError(f"the snippet to replace appears {current.count(find)} times in {rel} (must be once)")
            files[rel] = (before, current.replace(find, replace, 1))
        if len(files) > MAX_FILES:
            raise SelfRepairError(f"a fix may change at most {MAX_FILES} files")
        test_path, test_content = str(proposal.get("test_path", "")), proposal.get("test_content")
        if not TEST_PATH_RE.match(test_path):
            raise SelfRepairError("the regression test must be a new file tests/selfrepair/test_<name>.py")
        if (repo / test_path).exists():
            raise SelfRepairError(f"{test_path} already exists")
        if not isinstance(test_content, str) or "def test_" not in test_content or len(test_content) > MAX_SNIPPET:
            raise SelfRepairError("the regression test is missing or too large")
        try:
            compile(test_content, test_path, "exec")
        except SyntaxError as exc:
            raise SelfRepairError(f"the regression test isn't valid Python: {exc}") from None
        for rel, (_, after) in files.items():
            if rel.endswith(".py"):
                try:
                    compile(after, rel, "exec")
                except SyntaxError as exc:
                    raise SelfRepairError(f"the fixed {rel} isn't valid Python: {exc}") from None
        return files

    @staticmethod
    def unified_diff(proposal: Dict[str, Any], files: Dict[str, Tuple[str, str]]) -> str:
        out = []
        for rel, (before, after) in files.items():
            out.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        out.extend(difflib.unified_diff([], proposal["test_content"].splitlines(keepends=True),
                                        fromfile="/dev/null", tofile=f"b/{proposal['test_path']}"))
        return "".join(out)

    # ------------------------------------------------------------------ proof in the sandbox
    def _snapshot(self, dest: Path) -> None:
        for rel in self.tracked_files():
            src = self.repo / rel
            if not src.is_file() or src.is_symlink():
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)

    def verify(self, rid: str, proposal: Dict[str, Any], *, unisolated: bool = False) -> List[Dict[str, Any]]:
        from services.agents.sandbox import Workspace, python_cmd
        from packages.security import StopLatch
        work = Path(tempfile.mkdtemp(prefix=f"hood-selfrepair-{rid}-"))
        try:
            before, after = work / "before", work / "after"
            self._snapshot(before)
            files = self.check_proposal(proposal, repo=before)
            shutil.copytree(before, after)
            for rel, (_, new) in files.items():
                (after / rel).write_text(new, encoding="utf-8")
            for tree in (before, after):
                test = tree / proposal["test_path"]
                test.parent.mkdir(parents=True, exist_ok=True)
                test.write_text(proposal["test_content"], encoding="utf-8")
            latch = self.stop_latch or StopLatch()
            test_cmd = python_cmd("-m", "pytest", "-q", "-p", "no:cacheprovider", proposal["test_path"])
            limits = {"cpu_seconds": 1800, "memory_bytes": 4 * 1024 ** 3}
            checks = []
            r = Workspace(before, latch).run("reproduces_the_problem", test_cmd, timeout=300, unisolated=unisolated,
                                             **limits)
            ok = r.exit_code == 1
            checks.append({"name": "reproduces_the_problem", "passed": ok, "exit_code": r.exit_code,
                           "why": "the new test fails on today's code" if ok else
                           ("the new test already passes without the fix, so it doesn't prove the problem"
                            if r.exit_code == 0 else "the new test couldn't run on today's code"),
                           "output": r.output_tail[-3000:]})
            if ok:
                r = Workspace(after, latch).run("fix_makes_it_pass", test_cmd, timeout=300, unisolated=unisolated,
                                                **limits)
                checks.append({"name": "fix_makes_it_pass", "passed": r.exit_code == 0, "exit_code": r.exit_code,
                               "why": "the new test passes with the fix" if r.exit_code == 0 else
                               "the new test still fails with the fix", "output": r.output_tail[-3000:]})
            if all(c["passed"] for c in checks):
                r = Workspace(after, latch).run("whole_suite_still_passes", python_cmd(*self.suite_args),
                                                timeout=1800, unisolated=unisolated, **limits)
                checks.append({"name": "whole_suite_still_passes", "passed": r.exit_code == 0, "exit_code": r.exit_code,
                               "why": "HOOD's test suite passes with the fix" if r.exit_code == 0 else
                               "the fix breaks something else in HOOD", "output": r.output_tail[-3000:],
                               "duration_s": round(r.duration_ms / 1000)})
            return checks
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------------ apply / undo
    def apply(self, rid: str, proposal_sha: str, approver: str, is_root_owner: bool) -> Dict[str, Any]:
        if not is_root_owner:
            raise PermissionError("Only the Root Owner can change HOOD's code")
        if self.stop_latch is not None:
            self.stop_latch.check()
        row = self._row(rid)
        if row["state"] != "NEEDS_DECISION":
            raise SelfRepairConflict(f"This report is {row['state']}, not waiting for your decision")
        if not isinstance(proposal_sha, str) or proposal_sha != row["proposal_sha"]:
            raise SelfRepairConflict("Your approval doesn't match the fix shown; reload and review it again")
        proposal = json.loads(row["proposal"])
        try:
            files = self.check_proposal(proposal)
        except SelfRepairError as exc:
            raise SelfRepairConflict(f"HOOD's code changed since this fix was prepared ({exc}); report it again") from None
        blocked = [rel for rel in [*files, proposal["test_path"]] if not self._writable(self.repo / rel)]
        if blocked:
            raise SelfRepairConflict("HOOD's code is read-only on this machine (for example inside the container), so "
                                     "this fix can't be applied here: " + ", ".join(blocked) +
                                     ". Apply the change shown on your development copy instead.")
        restore = self.root / rid / "restore"
        restore.mkdir(parents=True, exist_ok=True)
        written: List[str] = []
        try:
            for rel, (before, after) in files.items():
                (restore / rel).parent.mkdir(parents=True, exist_ok=True)
                (restore / rel).write_text(before, encoding="utf-8")
                self._write(self.repo / rel, after)
                written.append(rel)
            test = self.repo / proposal["test_path"]
            test.parent.mkdir(parents=True, exist_ok=True)
            self._write(test, proposal["test_content"])
            problems = self._quick_check(list(files) + [proposal["test_path"]])
            if problems:
                raise SelfRepairError("; ".join(problems))
        except Exception as exc:
            for rel in written:
                self._write(self.repo / rel, (restore / rel).read_text(encoding="utf-8"))
            try:
                (self.repo / proposal["test_path"]).unlink()
            except OSError:
                pass
            self._set(rid, state="FAILED", error=f"Applying failed and was rolled back: {exc}")
            raise SelfRepairConflict(f"Applying failed and was rolled back: {exc}") from None
        patch = self.root / f"{rid}.patch"
        patch.write_text(row["diff"] or "", encoding="utf-8")
        applied = {"by": approver, "at": _now(), "files": list(files), "test": proposal["test_path"],
                   "python_changed": any(r.endswith(".py") for r in files), "patch": str(patch),
                   "after_sha": {rel: hashlib.sha256(after.encode()).hexdigest() for rel, (_, after) in files.items()}}
        self._set(rid, state="APPLIED", applied=applied, error=None)
        self._log(rid, f"Applied by {approver}" + (" (restart HOOD to load it)." if applied["python_changed"]
                                                   else " (reload the page)."))
        return self.get(rid)

    def undo(self, rid: str, actor: str, is_root_owner: bool) -> Dict[str, Any]:
        if not is_root_owner:
            raise PermissionError("Only the Root Owner can change HOOD's code")
        row = self._row(rid)
        if row["state"] != "APPLIED":
            raise SelfRepairConflict("Only an applied fix can be undone")
        applied = json.loads(row["applied"])
        restore = self.root / rid / "restore"
        changed = [rel for rel, sha in applied["after_sha"].items()
                   if not (self.repo / rel).is_file()
                   or hashlib.sha256((self.repo / rel).read_text(encoding="utf-8").encode()).hexdigest() != sha]
        if changed:
            raise SelfRepairConflict("These files changed after the fix was applied, so undoing could lose work: "
                                     + ", ".join(changed) + f". The originals are in {restore}.")
        for rel in applied["files"]:
            self._write(self.repo / rel, (restore / rel).read_text(encoding="utf-8"))
        try:
            (self.repo / applied["test"]).unlink()
        except OSError:
            pass
        self._set(rid, state="UNDONE")
        self._log(rid, f"Undone by {actor}" + (" (restart HOOD to load it)." if applied["python_changed"]
                                               else " (reload the page)."))
        return self.get(rid)

    def restart(self, actor: str) -> str:
        if self.restart_hook is None:
            raise SelfRepairConflict("Restart HOOD yourself (this HOOD wasn't started with hood_cli.py ui)")
        self.restart_hook()
        return f"HOOD restarts in a few seconds (requested by {actor}); reload the page then."

    @staticmethod
    def _writable(path: Path) -> bool:
        """The file (or, for a new file, its nearest existing folder) can be replaced in place."""
        folder = path.parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        return os.access(folder, os.W_OK) and (not path.exists() or os.access(path, os.W_OK))

    @staticmethod
    def _write(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".selfrepair-tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _quick_check(self, rels: List[str]) -> List[str]:
        problems = []
        for rel in rels:
            path = self.repo / rel
            if rel.endswith(".py"):
                try:
                    compile(path.read_text(encoding="utf-8"), rel, "exec")
                except SyntaxError as exc:
                    problems.append(f"{rel}: {exc}"[:240])
            elif rel.endswith(".js") and shutil.which("node"):
                r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=60)
                if r.returncode != 0:
                    problems.append(f"{rel}: {r.stderr[-200:]}")
        return problems
