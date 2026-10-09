"""Disposable per-mission workspace with governed file writes and process execution.

What this sandbox enforces (at the execution boundary, not in prompts):
- writes only under the role's allowed sub-tree, canonical path confinement,
  extension allowlist, size limits, no hidden or interpreter-hook files;
- commands come only from the engine's fixed allowlist (no shell, no agent argv);
- child processes get a scrubbed environment (no API keys, proxies or HOME),
  CPU/memory/file-size/descriptor limits, their own process group (killable on
  cancel or emergency stop) and, on Linux, a separate network namespace.

What it does NOT do: hide the host filesystem from child processes. Treat it as
defense in depth; a container or VM is required before running untrusted code
on a machine that holds real data. On platforms without ``unshare`` the runner
refuses to execute unless network isolation is explicitly waived.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from packages.security import confine_path, PathConfinementError, StopLatch
from .contracts import ROLE_WRITE_ROOTS, AgentRole, CheckResult, FileWrite, MAX_FILE_BYTES

ALLOWED_SUFFIXES = {".py", ".md", ".txt", ".json", ".html", ".css", ".js", ".toml", ".cfg", ".ini", ".csv"}
FORBIDDEN_NAMES = {"sitecustomize.py", "usercustomize.py", "pytest.ini", "tox.ini", "setup.cfg",
                   "pyproject.toml", "setup.py"}
IGNORED_DIRS = {"__pycache__", ".pytest_cache"}
OUTPUT_LIMIT = 64 * 1024


class SandboxViolation(PermissionError):
    pass


class SandboxUnavailable(RuntimeError):
    pass


def network_isolation_available() -> bool:
    if os.name != "posix" or not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _limits():  # runs in the child before exec (POSIX only)
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 ** 2, 32 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


class Workspace:
    def __init__(self, root: Path, stop_latch: Optional[StopLatch] = None, *,
                 require_network_isolation: bool = True):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stop_latch = stop_latch or StopLatch()
        self.require_network_isolation = require_network_isolation
        self._procs: Dict[int, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._netns = network_isolation_available()

    # ------------------------------------------------------------- files
    def check_write(self, role: AgentRole, rel_path: str) -> Path:
        try:
            target = confine_path(self.root, rel_path, label="workspace file")
        except PathConfinementError as exc:
            raise SandboxViolation(str(exc)) from None
        rel = target.relative_to(self.root)
        parts = rel.parts
        allowed_roots = ROLE_WRITE_ROOTS[role]
        if not parts or parts[0] not in allowed_roots:
            raise SandboxViolation(f"Role {role.value} may not write {rel_path} (allowed: {allowed_roots})")
        if any(p.startswith(".") for p in parts):
            raise SandboxViolation("Hidden files and directories are not writable")
        if target.suffix not in ALLOWED_SUFFIXES or target.name in FORBIDDEN_NAMES or target.suffix == ".pth":
            raise SandboxViolation(f"File type not permitted: {target.name}")
        if target.is_symlink():
            raise SandboxViolation("Refusing to write through a symlink")
        return target

    def apply(self, role: AgentRole, files: List[FileWrite]) -> List[dict]:
        """Validate every write first, then apply; returns per-file receipts."""
        self.stop_latch.check()
        planned = [(self.check_write(role, f.path), f.content.encode("utf-8")) for f in files]
        receipts = []
        for target, data in planned:
            if len(data) > MAX_FILE_BYTES:
                raise SandboxViolation(f"{target.name} exceeds {MAX_FILE_BYTES} bytes")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".hoodtmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            receipts.append({"path": target.relative_to(self.root).as_posix(),
                             "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        return receipts

    def listing(self, limit: int = 200) -> List[str]:
        out = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and not (set(path.relative_to(self.root).parts) & IGNORED_DIRS):
                out.append(path.relative_to(self.root).as_posix())
        return out[:limit]

    def read_files(self, max_total: int = 120_000) -> Dict[str, str]:
        files, total = {}, 0
        for rel in self.listing():
            data = (self.root / rel).read_text(encoding="utf-8", errors="replace")
            if total + len(data) > max_total:
                files[rel] = "<omitted: context limit>"
                continue
            total += len(data)
            files[rel] = data
        return files

    def digest(self) -> str:
        h = hashlib.sha256()
        for rel in self.listing(limit=10_000):
            h.update(rel.encode() + b"\0" + hashlib.sha256((self.root / rel).read_bytes()).digest())
        return h.hexdigest()

    # ------------------------------------------------------------- processes
    def _env(self) -> Dict[str, str]:
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.root), "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONPATH": str(self.root),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "TMPDIR": str(self.root)}

    def run(self, name: str, argv: List[str], timeout: int = 120) -> CheckResult:
        """Run an engine-built command; the agent never supplies argv."""
        self.stop_latch.check()
        if os.name != "posix":
            raise SandboxUnavailable("Process sandbox is only implemented for POSIX hosts")
        if self.require_network_isolation and not self._netns:
            raise SandboxUnavailable("Network namespace isolation (unshare -rn) is unavailable")
        launcher = str(Path(__file__).with_name("netns_launcher.py"))
        cmd = (["unshare", "-rn", "--", sys.executable, "-I", launcher] if self._netns else []) + argv
        started = time.monotonic()
        proc = subprocess.Popen(cmd, cwd=self.root, env=self._env(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, preexec_fn=_limits)
        with self._lock:
            self._procs[proc.pid] = proc
        try:
            out, _ = proc.communicate(timeout=timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            self._kill(proc)
            out, _ = proc.communicate()
            code = None
        finally:
            with self._lock:
                self._procs.pop(proc.pid, None)
        text = (out or b"")[-OUTPUT_LIMIT:].decode("utf-8", errors="replace")
        return CheckResult(name=name, command=argv, exit_code=code, passed=code == 0,
                           output_tail=text[-4000:], duration_ms=int((time.monotonic() - started) * 1000))

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def kill_all(self) -> int:
        """Kill every running child process group (cancel / emergency stop)."""
        with self._lock:
            procs = list(self._procs.values())
        for proc in procs:
            self._kill(proc)
        return len(procs)

    @property
    def network_isolated(self) -> bool:
        return self._netns


def python_cmd(*args: str) -> List[str]:
    return [sys.executable, "-B", *args]
