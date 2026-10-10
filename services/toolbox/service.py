"""Toolbox: installs what a mission needs, inside WSL2/Linux, with the owner's permission.

- Owner approval is recorded once per tool (and covers the tool's dependencies, listed in
  the same request). After that HOOD may reuse and update the tool without asking.
- System packages go through the root-owned helper (scripts/wsl/hood-pkg), which accepts only
  the packages in the catalog: HOOD never holds root rights or the owner's password.
- Downloads go through HOOD's egress firewall (the owner's approval allows the tool's hosts),
  are verified against the publisher's checksums before unpacking, and are unpacked safely into
  <HOOD_DATA_DIR>/tools. Nothing downloaded is run by the toolbox itself.
- The emergency stop halts installs between steps.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .catalog import PROFILE_TOOLS, TOOLS, Tool, with_dependencies

HELPER_DEFAULT = "/usr/local/sbin/hood-pkg"
MAX_ARCHIVE = 120 * 1024 * 1024
MAX_FILE = 30 * 1024 * 1024
ENABLE_HINT = ("Installs aren't switched on yet. In Ubuntu (WSL2) run once: "
               "sudo bash ~/hood/scripts/wsl/enable_installs.sh  (it asks your Linux password; HOOD never sees it)")


class ToolUnavailable(RuntimeError):
    """This computer can't install tools right now (Windows, or installs not switched on)."""


class ToolNotApproved(PermissionError):
    """The owner hasn't allowed this tool yet: HOOD must ask first."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Toolbox:
    def __init__(self, data_dir: Path, *, firewall: Any = None, stop_latch: Any = None,
                 runner: Optional[Callable[[List[str], int], Tuple[int, str]]] = None,
                 fetcher: Optional[Callable[[str, int], bytes]] = None):
        self.root = Path(data_dir) / "tools"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.firewall = firewall
        self.stop_latch = stop_latch
        self._run = runner or self._subprocess_run
        self._fetch = fetcher or self._http_get
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ paths
    def path(self, tool_id: str) -> Path:
        return {"wordpress": self.root / "wordpress",
                "wp_sqlite": self.root / "wp-sqlite" / "sqlite-database-integration",
                "wp_cli": self.root / "bin" / "wp-cli.phar"}.get(tool_id, self.root / tool_id)

    # ------------------------------------------------------------------ platform
    @staticmethod
    def is_wsl() -> bool:
        return "microsoft" in platform.release().lower()

    def platform_problem(self) -> Optional[str]:
        if os.name == "nt":
            return ("Installs happen only inside WSL2 (your setting), and HOOD is running on Windows itself. "
                    "Start HOOD in WSL2 (scripts\\windows\\hood-wsl.ps1, see docs/WSL2.md) and ask again.")
        if not sys.platform.startswith("linux"):
            return "Installs are set up for WSL2 / Ubuntu only."
        return None

    def helper(self) -> str:
        return os.environ.get("HOOD_PKG_HELPER") or HELPER_DEFAULT

    def helper_problem(self) -> Optional[str]:
        """Why system packages can't be installed yet (None when the helper is ready)."""
        if self.platform_problem():
            return self.platform_problem()
        helper = self.helper()
        if not os.path.isfile(helper):
            return ENABLE_HINT
        rc, _ = self._run(self._helper_argv("check"), 30)
        return None if rc == 0 else ENABLE_HINT

    def _helper_argv(self, *args: str) -> List[str]:
        helper = self.helper()
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return [helper, *args]
        return ["sudo", "-n", helper, *args]

    # ------------------------------------------------------------------ state
    def _state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"tools": {}, "history": []}

    def _save(self, state: Dict[str, Any]) -> None:
        state["history"] = state.get("history", [])[-200:]
        tmp = self.state_path.with_name("state.json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _record(self, actor: str, action: str, tool_id: str, result: str) -> None:
        with self._lock:
            state = self._state()
            state.setdefault("history", []).append({"at": _now(), "actor": actor, "action": action,
                                                    "tool": tool_id, "result": result[:300]})
            self._save(state)

    def approved(self, tool_id: str) -> bool:
        return bool(self._state().get("tools", {}).get(tool_id, {}).get("approved_by"))

    # ------------------------------------------------------------------ detection
    def detect(self, tool_id: str) -> Dict[str, Any]:
        tool = TOOLS[tool_id]
        try:
            if tool.kind == "apt":
                return self._detect_apt(tool)
            if tool_id == "wordpress":
                version = self._php_var(self.path(tool_id) / "wp-includes" / "version.php", "wp_version")
                return {"installed": version is not None, "version": version}
            if tool_id == "wp_sqlite":
                head = (self.path(tool_id) / "load.php")
                version = None
                if head.is_file():
                    m = re.search(r"^\s*\*\s*Version:\s*([\w.\-]+)", head.read_text(errors="replace"), re.M)
                    version = m.group(1) if m else "unknown"
                return {"installed": version is not None, "version": version}
            if tool_id == "wp_cli":
                exists = self.path(tool_id).is_file()
                return {"installed": exists, "version": self._state().get("tools", {}).get(tool_id, {})
                        .get("version") if exists else None}
        except OSError as exc:
            return {"installed": False, "version": None, "detail": str(exc)}
        return {"installed": False, "version": None}

    def _detect_apt(self, tool: Tool) -> Dict[str, Any]:
        if os.name == "nt" or not tool.binary or not shutil.which(tool.binary):
            return {"installed": False, "version": None}
        if tool.id == "php":
            rc, out = self._run(["php", "-r", "echo PHP_VERSION;"], 20)
            version = out.strip().splitlines()[-1] if rc == 0 and out.strip() else None
            rc, mods = self._run(["php", "-m"], 20)
            have = {m.strip().lower() for m in mods.splitlines()} if rc == 0 else set()
            missing = [m for m in tool.php_modules if m not in have]
            if missing:
                return {"installed": False, "version": version,
                        "detail": "PHP is here but missing: " + ", ".join(missing)}
            return {"installed": True, "version": version}
        rc, out = self._run([tool.binary, "--version"], 20)
        first = out.strip().splitlines()[0] if out.strip() else ""
        return {"installed": True, "version": first[:80] or None}

    @staticmethod
    def _php_var(path: Path, name: str) -> Optional[str]:
        if not path.is_file():
            return None
        m = re.search(r"\$" + name + r"\s*=\s*'([^']+)'", path.read_text(errors="replace"))
        return m.group(1) if m else "unknown"

    # ------------------------------------------------------------------ views
    def status(self) -> Dict[str, Any]:
        state = self._state().get("tools", {})
        tools = []
        for tid, tool in TOOLS.items():
            seen = self.detect(tid)
            rec = state.get(tid, {})
            tools.append({"id": tid, "name": tool.name, "purpose": tool.purpose, "size": tool.size,
                          "source": tool.source, "kind": tool.kind, "requires": list(tool.requires),
                          "packages": list(tool.packages), "installed": seen["installed"],
                          "version": seen.get("version"), "detail": seen.get("detail"),
                          "approved": bool(rec.get("approved_by")), "approved_by": rec.get("approved_by"),
                          "approved_at": rec.get("approved_at"), "installed_by_hood": bool(rec.get("installed_by_hood")),
                          "last_error": rec.get("last_error")})
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["started"], reverse=True)[:10]
        return {"platform_problem": self.platform_problem(), "helper_problem": self.helper_problem(),
                "wsl": self.is_wsl(), "tools": tools, "jobs": [dict(j) for j in jobs],
                "history": self._state().get("history", [])[-30:]}

    def needs(self, tool_ids) -> Dict[str, Any]:
        """What is missing for these tools (with dependencies), and whether the owner must be asked."""
        order = with_dependencies(list(tool_ids))
        missing = [t for t in order if not self.detect(t)["installed"]]
        unapproved = [t for t in missing if not self.approved(t)]
        problem = self.platform_problem()
        if not problem and any(TOOLS[t].kind == "apt" for t in missing):
            problem = self.helper_problem()
        return {"tools": order, "missing": missing, "unapproved": unapproved,
                "names": {t: TOOLS[t].name for t in order}, "ready": not missing,
                "problem": problem if missing else None, "running": self._running_job(missing)}

    def needs_for_profile(self, profile: str) -> Optional[Dict[str, Any]]:
        tools = PROFILE_TOOLS.get(profile)
        return self.needs(tools) if tools else None

    def _running_job(self, tool_ids: List[str]) -> Optional[str]:
        with self._lock:
            for job in self._jobs.values():
                if job["state"] == "running" and set(job["tools"]) & set(tool_ids):
                    return job["id"]
        return None

    def job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return dict(self._jobs[job_id])

    # ------------------------------------------------------------------ owner actions
    def request_install(self, tool_ids: List[str], actor: str) -> Dict[str, Any]:
        """The owner allows these tools (and their dependencies) once, then HOOD installs what's missing."""
        order = with_dependencies(list(tool_ids))
        if self.platform_problem():
            raise ToolUnavailable(self.platform_problem())
        with self._lock:
            state = self._state()
            for tid in order:
                rec = state.setdefault("tools", {}).setdefault(tid, {})
                if not rec.get("approved_by"):
                    rec.update({"approved_by": actor, "approved_at": _now()})
                    state.setdefault("history", []).append({"at": _now(), "actor": actor, "action": "approved",
                                                            "tool": tid, "result": "allowed once by the owner"})
            self._save(state)
        self._allow_hosts(order, actor)
        return self._start([t for t in order if not self.detect(t)["installed"]], actor, "install")

    def ensure(self, tool_ids: List[str], actor: str = "hood") -> Optional[Dict[str, Any]]:
        """Install approved-but-missing tools without asking (owner's rule). Unapproved -> ToolNotApproved."""
        need = self.needs(tool_ids)
        if need["unapproved"]:
            raise ToolNotApproved("Needs your OK first: " + ", ".join(TOOLS[t].name for t in need["unapproved"]))
        if not need["missing"]:
            return None
        if need["problem"]:
            raise ToolUnavailable(need["problem"])
        return self._start(need["missing"], actor, "install")

    def update(self, tool_id: str, actor: str) -> Dict[str, Any]:
        if not self.approved(tool_id):
            raise ToolNotApproved(f"{TOOLS[tool_id].name} hasn't been allowed yet")
        if self.platform_problem():
            raise ToolUnavailable(self.platform_problem())
        return self._start([tool_id], actor, "update")

    def remove(self, tool_id: str, actor: str) -> Dict[str, Any]:
        tool = TOOLS[tool_id]
        with self._lock:
            state = self._state()
            rec = state.get("tools", {}).get(tool_id, {})
        if tool.kind == "apt":
            if not rec.get("installed_by_hood"):
                raise PermissionError(f"{tool.name} was already on this computer before HOOD installed anything; "
                                      "HOOD only removes what it installed.")
            if self.helper_problem():
                raise ToolUnavailable(self.helper_problem())
            rc, out = self._run(self._helper_argv("remove", *tool.packages), 900)
            if rc != 0:
                raise RuntimeError(f"Removing {tool.name} failed: {out[-400:]}")
        else:
            target = self.path(tool_id)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        with self._lock:
            state = self._state()
            state.get("tools", {}).pop(tool_id, None)    # removing it also withdraws the approval
            self._save(state)
        self._record(actor, "removed", tool_id, "removed; HOOD will ask again before reinstalling")
        return self.status()

    # ------------------------------------------------------------------ jobs
    def _start(self, tool_ids: List[str], actor: str, action: str) -> Dict[str, Any]:
        job = {"id": "job_" + uuid.uuid4().hex[:12], "tools": tool_ids, "action": action, "actor": actor,
               "state": "running" if tool_ids else "done", "log": [], "started": _now(), "finished": None,
               "error": None}
        with self._lock:
            self._jobs[job["id"]] = job
        if tool_ids:
            threading.Thread(target=self._work, args=(job,), daemon=True).start()
        else:
            job["finished"] = _now()
            job["log"].append("Everything needed is already installed.")
        return dict(job)

    def run_job_now(self, job_id: str) -> Dict[str, Any]:
        """Wait for a job (tests and CLI)."""
        while self.job(job_id)["state"] == "running":
            time.sleep(0.05)
        return self.job(job_id)

    def _log(self, job: Dict[str, Any], line: str) -> None:
        with self._lock:
            job["log"].append(line)
            job["log"] = job["log"][-200:]

    def _work(self, job: Dict[str, Any]) -> None:
        try:
            for tid in job["tools"]:
                if self.stop_latch is not None:
                    self.stop_latch.check()
                tool = TOOLS[tid]
                self._log(job, f"{'Updating' if job['action'] == 'update' else 'Installing'} {tool.name} "
                               f"from {tool.source}…")
                version = self._install_one(tool, job)
                with self._lock:
                    state = self._state()
                    rec = state.setdefault("tools", {}).setdefault(tid, {})
                    rec.update({"installed_by_hood": True, "installed_at": _now(), "version": version,
                                "last_error": None})
                    self._save(state)
                self._record(job["actor"], job["action"], tid, f"ok ({version or 'version unknown'})")
                self._log(job, f"{tool.name} ready{(' (' + version + ')') if version else ''}.")
            job["state"] = "done"
        except Exception as exc:  # reported to the owner; nothing half-installed is used
            job["state"] = "failed"
            job["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
            self._log(job, "Stopped: " + job["error"])
            failed = next((t for t in job["tools"] if not self.detect(t)["installed"]), None)
            if failed:
                with self._lock:
                    state = self._state()
                    state.setdefault("tools", {}).setdefault(failed, {})["last_error"] = job["error"]
                    self._save(state)
                self._record(job["actor"], job["action"], failed, "failed: " + job["error"])
        finally:
            job["finished"] = _now()

    def _install_one(self, tool: Tool, job: Dict[str, Any]) -> Optional[str]:
        if tool.kind == "apt":
            problem = self.helper_problem()
            if problem:
                raise ToolUnavailable(problem)
            verb = "upgrade" if job["action"] == "update" else "install"
            rc, out = self._run(self._helper_argv(verb, *tool.packages), 1800)
            self._log(job, out.strip().splitlines()[-1][:200] if out.strip() else f"apt exit code {rc}")
            if rc != 0:
                raise RuntimeError(f"apt could not {verb} {', '.join(tool.packages)} (exit {rc}): {out[-300:]}")
            seen = self.detect(tool.id)
            if not seen["installed"]:
                raise RuntimeError(f"{tool.name} still isn't usable after installing: {seen.get('detail', '')}")
            return seen.get("version")
        if tool.kind == "archive":
            return self._install_wordpress(tool, job)
        if tool.kind == "file":
            return self._install_file(tool, job)
        if tool.kind == "wp_plugin":
            return self._install_wp_plugin(tool, job)
        raise ValueError(f"Unknown tool kind {tool.kind}")

    # ------------------------------------------------------------------ downloads
    @staticmethod
    def _digest(algo: str, data: bytes) -> str:
        return hashlib.new(algo, data).hexdigest()

    def _checksum(self, url: str) -> str:
        text = self._fetch(url, 4096).decode("ascii", errors="replace").strip()
        token = text.split()[0] if text else ""
        if not re.fullmatch(r"[0-9a-fA-F]{40,128}", token):
            raise RuntimeError(f"The published checksum at {url} is not readable")
        return token.lower()

    def _install_wordpress(self, tool: Tool, job) -> str:
        expected = self._checksum(tool.checksum_url)
        self._log(job, "Downloading WordPress…")
        data = self._fetch(tool.url, MAX_ARCHIVE)
        if self._digest(tool.algo, data) != expected:
            raise RuntimeError("WordPress download doesn't match wordpress.org's published checksum; not installed")
        self._log(job, "Checksum matches wordpress.org's. Unpacking…")
        tmp = self.root / (".tmp-" + uuid.uuid4().hex[:8])
        tmp.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                tar.extractall(tmp, filter="data")      # refuses absolute paths, links out, devices
            core = tmp / "wordpress"
            if not (core / "wp-includes" / "version.php").is_file():
                raise RuntimeError("The WordPress archive doesn't contain wordpress/wp-includes/version.php")
            self._replace(core, self.path("wordpress"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return self._php_var(self.path("wordpress") / "wp-includes" / "version.php", "wp_version")

    def _install_file(self, tool: Tool, job) -> str:
        expected = self._checksum(tool.checksum_url)
        data = self._fetch(tool.url, MAX_FILE)
        if self._digest(tool.algo, data) != expected:
            raise RuntimeError(f"{tool.name} download doesn't match its published checksum; not installed")
        target = self.path(tool.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        self._log(job, f"Checksum matches; saved {target.name}.")
        return "sha512 " + expected[:12]

    def _install_wp_plugin(self, tool: Tool, job) -> str:
        query = urllib.parse.urlencode({"action": "plugin_information", "request[slug]": tool.slug})
        info = json.loads(self._fetch("https://api.wordpress.org/plugins/info/1.2/?" + query, 2_000_000))
        version, link = str(info.get("version", "")), str(info.get("download_link", ""))
        if not re.fullmatch(r"[\w.\-]{1,32}", version) or not link.startswith("https://downloads.wordpress.org/"):
            raise RuntimeError("The plugin directory returned unexpected download information")
        sums = json.loads(self._fetch(f"https://downloads.wordpress.org/plugin-checksums/{tool.slug}/{version}.json",
                                      2_000_000)).get("files", {})
        if not sums:
            raise RuntimeError("wordpress.org has no checksums for this plugin version; not installed")
        data = self._fetch(link, MAX_ARCHIVE)
        tmp = self.root / (".tmp-" + uuid.uuid4().hex[:8])
        tmp.mkdir()
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info_ in zf.infolist():
                    name = info_.filename
                    if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                        raise RuntimeError(f"Unsafe path in plugin archive: {name}")
                zf.extractall(tmp)
            folder = tmp / tool.slug
            files = [p for p in folder.rglob("*") if p.is_file()]
            for p in files:
                rel = p.relative_to(folder).as_posix()
                want = sums.get(rel)
                if want is None:
                    raise RuntimeError(f"{rel} is not in wordpress.org's checksum list; not installed")
                wanted = {w.get("sha256") for w in want} if isinstance(want, list) else {want.get("sha256")}
                if self._digest("sha256", p.read_bytes()) not in wanted:
                    raise RuntimeError(f"{rel} doesn't match wordpress.org's checksum; not installed")
            self._log(job, f"All {len(files)} plugin files match wordpress.org's checksums.")
            self._replace(folder, self.path(tool.id))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return version

    @staticmethod
    def _replace(src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        old = dest.with_name(dest.name + ".old")
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        if dest.exists():
            os.replace(dest, old)
        os.replace(src, dest)
        shutil.rmtree(old, ignore_errors=True)

    def _allow_hosts(self, tool_ids: List[str], actor: str) -> None:
        if self.firewall is None:
            return
        have = {r.get("host") for r in self.firewall.list_rules()}
        for tid in tool_ids:
            for host in TOOLS[tid].hosts:
                if host not in have:
                    # The owner allowing the tool is the owner allowing its official download host.
                    self.firewall.allow(host, [443], note=f"Toolbox: {TOOLS[tid].name} (allowed by the owner)",
                                        added_by=actor, is_root_owner=True)
                    have.add(host)

    def _http_get(self, url: str, limit: int) -> bytes:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https":
            raise RuntimeError("Toolbox downloads must use https")
        firewall = self.firewall

        class _Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                target = urllib.parse.urlsplit(newurl)
                if target.scheme != "https":
                    raise RuntimeError("Refusing a non-https redirect")
                if firewall is not None:
                    firewall.enforce(target.hostname or "", 443, purpose="toolbox download")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        if firewall is not None:
            firewall.enforce(parts.hostname or "", 443, purpose="toolbox download")
        opener = urllib.request.build_opener(_Redirect)
        last = None
        for attempt in range(3):   # connections can be cut mid-transfer: read to the end and check the length
            req = urllib.request.Request(url, headers={"User-Agent": "HOOD-Toolbox/1.0"})
            try:
                with opener.open(req, timeout=180) as resp:
                    expected = resp.headers.get("Content-Length")
                    chunks, size = [], 0
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limit:
                            raise RuntimeError(f"Download larger than {limit // (1024 * 1024)} MB; refused")
                        chunks.append(chunk)
                data = b"".join(chunks)
                if expected and expected.isdigit() and int(expected) != len(data):
                    raise ConnectionError(f"download cut short ({len(data)} of {expected} bytes)")
                return data
            except (ConnectionError, OSError, http.client.HTTPException) as exc:
                last = exc
                time.sleep(1 + attempt)
        raise RuntimeError(f"Download failed after 3 attempts: {last}")

    @staticmethod
    def _subprocess_run(argv: List[str], timeout: int) -> Tuple[int, str]:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                  env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)
