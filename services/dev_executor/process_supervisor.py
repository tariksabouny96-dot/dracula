"""
HOOD Development Executor - Process Supervisor
Manages local background development services with port conflict checks, readiness health-checks,
log tailing, and emergency termination.
Governed by Master System Specification Sections 9, 14, 15 & V0.3 Development Executor Spec.
"""

import os
import sys
import time
import socket
import signal
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from packages.config.paths import store_dir


class ManagedProcessInfo(BaseModel):
    service_id: str
    command: str
    cwd: str
    pid: Optional[int] = None
    port: Optional[int] = None
    started_at: str
    status: str  # "starting", "running", "stopped", "failed"
    exit_code: Optional[int] = None
    readiness_url: Optional[str] = None
    is_ready: bool = False
    log_file: str
    error: Optional[str] = None


class ProcessSupervisor:
    """Safely launches, monitors, checks health, and terminates local development services."""

    def __init__(self, log_dir: Optional[Path] = None):
        self.log_dir = (log_dir or store_dir(None, "artifacts/dev_logs")).resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.processes: Dict[str, subprocess.Popen] = {}
        self.process_info: Dict[str, ManagedProcessInfo] = {}
        self._lock = threading.Lock()

    @staticmethod
    def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
        """Checks if a local TCP port is already open/in use."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            result = s.connect_ex((host, port))
            return result == 0

    @staticmethod
    def find_free_port(start_port: int = 8000, max_port: int = 9000) -> int:
        """Finds an unused local port starting from start_port."""
        for port in range(start_port, max_port):
            if not ProcessSupervisor.is_port_in_use(port):
                return port
        raise RuntimeError("No free port available in range.")

    def start_service(
        self,
        service_id: str,
        command: List[str] | str,
        cwd: Path,
        port: Optional[int] = None,
        readiness_url: Optional[str] = None,
        timeout_seconds: int = 15,
        env: Optional[Dict[str, str]] = None
    ) -> ManagedProcessInfo:
        """
        Starts a local development server process in background.
        Performs port pre-check to prevent hijacking external processes or collisions.
        """
        with self._lock:
            if service_id in self.processes and self.processes[service_id].poll() is None:
                raise RuntimeError(f"Service '{service_id}' is already running with PID {self.processes[service_id].pid}")

            # Check port collision
            if port is not None and self.is_port_in_use(port):
                raise RuntimeError(f"Port {port} is already in use by another process. Refusing collision.")

            log_file = self.log_dir / f"{service_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
            log_handle = open(log_file, "w", encoding="utf-8")

            process_env = os.environ.copy()
            # Ensure workspace root and cwd are in PYTHONPATH for local imports
            existing_pythonpath = process_env.get("PYTHONPATH", "")
            ws_str = str(cwd.resolve())
            process_env["PYTHONPATH"] = f"{ws_str};{existing_pythonpath}" if existing_pythonpath else ws_str
            if env:
                process_env.update(env)

            # Start subprocess
            proc = subprocess.Popen(
                command,
                cwd=str(cwd.resolve()),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=process_env,
                shell=(isinstance(command, str) and sys.platform == "win32")
            )

            info = ManagedProcessInfo(
                service_id=service_id,
                command=str(command),
                cwd=str(cwd),
                pid=proc.pid,
                port=port,
                started_at=datetime.now(timezone.utc).isoformat(),
                status="starting",
                readiness_url=readiness_url,
                log_file=str(log_file)
            )
            self.processes[service_id] = proc
            self.process_info[service_id] = info

        # Wait for readiness if URL provided or port check
        if readiness_url or port:
            is_ready = self._wait_for_readiness(service_id, readiness_url, port, timeout_seconds)
            info.is_ready = is_ready
            info.status = "running" if is_ready else ("failed" if proc.poll() is not None else "running_unconfirmed")
        else:
            time.sleep(1.0)
            if proc.poll() is not None:
                info.status = "failed"
                info.exit_code = proc.poll()
            else:
                info.status = "running"

        return info

    def _wait_for_readiness(
        self,
        service_id: str,
        readiness_url: Optional[str],
        port: Optional[int],
        timeout_seconds: int
    ) -> bool:
        """Polls until the server responds on HTTP or TCP port."""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            proc = self.processes.get(service_id)
            if proc and proc.poll() is not None:
                return False

            if readiness_url:
                try:
                    req = urllib.request.Request(readiness_url, headers={"User-Agent": "HOOD-Readiness-Check"})
                    with urllib.request.urlopen(req, timeout=1.0) as resp:
                        if resp.status in (200, 301, 302, 404):
                            return True
                except Exception:
                    pass
            elif port:
                if self.is_port_in_use(port):
                    return True

            time.sleep(0.5)
        return False

    def stop_service(self, service_id: str) -> ManagedProcessInfo:
        """Gracefully halts and terminates a managed process."""
        with self._lock:
            proc = self.processes.get(service_id)
            info = self.process_info.get(service_id)

            if not proc or not info:
                raise KeyError(f"No supervised service found with ID '{service_id}'")

            if proc.poll() is None:
                # Try graceful termination
                try:
                    proc.terminate()
                    proc.wait(timeout=3.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass

            info.status = "stopped"
            info.exit_code = proc.poll()
            return info

    def stop_all(self) -> List[ManagedProcessInfo]:
        """Terminates all supervised development processes. Used during emergency stop or cleanup."""
        results = []
        with self._lock:
            service_ids = list(self.processes.keys())

        for sid in service_ids:
            try:
                results.append(self.stop_service(sid))
            except Exception:
                pass
        return results

    def get_service_logs(self, service_id: str, max_lines: int = 100) -> str:
        """Reads recent logs from service logfile."""
        info = self.process_info.get(service_id)
        if not info or not Path(info.log_file).exists():
            return ""
        try:
            lines = Path(info.log_file).read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(lines[-max_lines:])
        except Exception as e:
            return f"Error reading logs: {e}"

    def get_status(self, service_id: str) -> Optional[ManagedProcessInfo]:
        proc = self.processes.get(service_id)
        info = self.process_info.get(service_id)
        if proc and info:
            if proc.poll() is not None and info.status == "running":
                info.status = "stopped"
                info.exit_code = proc.poll()
        return info
