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

"Run on my PC" (``unisolated=True``) exists for hosts with no sandbox (Windows):
the owner approves the exact workspace contents and fixed commands first; the
child still gets a scrubbed environment and a time limit, but NO network or
filesystem isolation. The engine only uses it after that recorded approval.
"""
from __future__ import annotations

import hashlib
import functools
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from packages.security import confine_path, PathConfinementError, StopLatch
from .contracts import ROLE_WRITE_ROOTS, AgentRole, CheckResult, FileWrite, MAX_FILE_BYTES

ALLOWED_SUFFIXES = {".py", ".md", ".txt", ".json", ".html", ".css", ".js", ".toml", ".cfg", ".ini", ".csv",
                    ".php"}  # .php: WordPress theme templates (run only inside the sandbox)
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


DEFAULT_CPU_SECONDS = 120
DEFAULT_MEMORY_BYTES = 2 * 1024 ** 3


def _limits(cpu_seconds: int = DEFAULT_CPU_SECONDS, memory_bytes: int = DEFAULT_MEMORY_BYTES):
    """Runs in the child before exec (POSIX only). Same isolation for every run; only the CPU-time
    and address-space budget can be raised by the engine for a known long job (HOOD's own test
    suite for a self-repair), never by agent-written code."""
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 ** 2, 32 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


# On Windows, HOOD's own Linux sandbox (services/toolbox/wsl.py), attached at start-up.
_WINDOWS_SANDBOX: Any = None


def set_windows_sandbox(sandbox: Any) -> None:
    global _WINDOWS_SANDBOX
    _WINDOWS_SANDBOX = sandbox


def _on_windows() -> bool:
    return os.name != "posix"


def windows_sandbox_ready() -> bool:
    return _on_windows() and _WINDOWS_SANDBOX is not None and _WINDOWS_SANDBOX.ready()


def sandbox_problem(require_network_isolation: bool = True) -> Optional[str]:
    """Why sandboxed execution is impossible on this host, or None when it is available."""
    if _on_windows():
        if windows_sandbox_ready():
            return None
        if _WINDOWS_SANDBOX is not None:
            return _WINDOWS_SANDBOX.problem()
        return "Windows has no agent sandbox (it needs Linux or WSL2)"
    if require_network_isolation and not network_isolation_available():
        return "Linux network isolation (unshare -rn) is not available on this host"
    return None


class Workspace:
    def __init__(self, root: Path, stop_latch: Optional[StopLatch] = None, *,
                 require_network_isolation: bool = True, write_roots: Optional[dict] = None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stop_latch = stop_latch or StopLatch()
        self.require_network_isolation = require_network_isolation
        self.write_roots = write_roots or ROLE_WRITE_ROOTS
        self._procs: Dict[int, subprocess.Popen] = {}
        self._lock = threading.Lock()
        self._netns = network_isolation_available()
        self._wsl_runs = 0

    # ------------------------------------------------------------- files
    def check_write(self, role: AgentRole, rel_path: str) -> Path:
        try:
            target = confine_path(self.root, rel_path, label="workspace file")
        except PathConfinementError as exc:
            raise SandboxViolation(str(exc)) from None
        rel = target.relative_to(self.root)
        parts = rel.parts
        allowed_roots = self.write_roots[role]
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

    def _env_unisolated(self) -> Dict[str, str]:
        """Scrubbed environment for an owner-approved run on the host (no API keys, proxies or profile)."""
        env = self._env()
        if os.name == "nt":
            system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env.update({"PATH": os.pathsep.join([str(Path(sys.executable).parent), system_root + r"\System32",
                                                 system_root]),
                        "SYSTEMROOT": system_root, "TEMP": str(self.root), "TMP": str(self.root),
                        "USERPROFILE": str(self.root), "PYTHONIOENCODING": "utf-8"})
        return env

    def run(self, name: str, argv: List[str], timeout: int = 120, *, unisolated: bool = False,
            extra_writable: Sequence[str] = (), extra_readonly: Sequence[str] = (),
            cpu_seconds: int = DEFAULT_CPU_SECONDS, memory_bytes: int = DEFAULT_MEMORY_BYTES) -> CheckResult:
        """Run an engine-built command; the agent never supplies argv.

        ``unisolated`` runs it directly on this computer (owner-approved "Run on my PC"):
        scrubbed environment and time limit, but no network or filesystem isolation.
        """
        self.stop_latch.check()
        cpu_seconds = max(1, min(int(cpu_seconds), 3600))
        memory_bytes = max(256 * 1024 ** 2, min(int(memory_bytes), 8 * 1024 ** 3))
        if not unisolated and windows_sandbox_ready():
            return self._run_in_wsl(name, argv, timeout, extra_writable, extra_readonly, cpu_seconds, memory_bytes)
        if unisolated:
            cmd = list(argv)
            env = self._env_unisolated()
        else:
            if os.name != "posix":
                raise SandboxUnavailable("Process sandbox is only implemented for POSIX hosts")
            if self.require_network_isolation and not self._netns:
                raise SandboxUnavailable("Network namespace isolation (unshare -rn) is unavailable")
            launcher = str(Path(__file__).with_name("netns_launcher.py"))
            cmd = (["unshare", "-rn", "--", sys.executable, "-I", launcher] if self._netns else []) + argv
            env = self._env()
        started = time.monotonic()
        if os.name == "posix":
            platform_kw = {"start_new_session": True,
                           "preexec_fn": functools.partial(_limits, cpu_seconds, memory_bytes)}
        else:  # Windows: own process group, no console window; killed as a tree on timeout/cancel
            platform_kw = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                           | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        proc = subprocess.Popen(cmd, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **platform_kw)
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

    def _run_in_wsl(self, name: str, argv: List[str], timeout: int, extra_writable: Sequence[str],
                    extra_readonly: Sequence[str], cpu_seconds: int = DEFAULT_CPU_SECONDS,
                    memory_bytes: int = DEFAULT_MEMORY_BYTES) -> CheckResult:
        """Windows: run inside HOOD's own Linux sandbox. Only the mission folder (and any extra folders
        the engine names) are visible; Windows drives are hidden; no network; resource limits."""
        inner = list(argv)
        if inner and os.path.normcase(os.path.abspath(inner[0])) == os.path.normcase(os.path.abspath(sys.executable)):
            inner[0] = "/usr/bin/python3"           # the sandbox's own Python
        launcher = str(Path(__file__).with_name("netns_launcher.py"))
        started = time.monotonic()
        with self._lock:
            self._wsl_runs += 1
        try:
            code, out = _WINDOWS_SANDBOX.run_isolated(
                inner, str(self.root), [str(self.root), *extra_writable],
                [str(Path(launcher).parent), *extra_readonly], launcher, timeout,
                cpu=cpu_seconds, memory_bytes=memory_bytes)
        finally:
            with self._lock:
                self._wsl_runs -= 1
        text = out[-OUTPUT_LIMIT:].decode("utf-8", errors="replace")
        return CheckResult(name=name, command=argv, exit_code=code, passed=code == 0,
                           output_tail=text[-4000:], duration_ms=int((time.monotonic() - started) * 1000))

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if os.name != "posix":
            try:  # the whole tree: pytest may have started children
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                proc.kill()
            except OSError:
                pass
            return
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def kill_all(self) -> int:
        """Kill every running child process group (cancel / emergency stop)."""
        with self._lock:
            procs = list(self._procs.values())
            wsl_runs = self._wsl_runs
        if wsl_runs and _WINDOWS_SANDBOX is not None:
            _WINDOWS_SANDBOX.terminate()             # stops everything inside HOOD's Linux sandbox
        for proc in procs:
            self._kill(proc)
        return len(procs)

    @property
    def network_isolated(self) -> bool:
        return self._netns or windows_sandbox_ready()


def python_cmd(*args: str) -> List[str]:
    return [sys.executable, "-B", *args]
