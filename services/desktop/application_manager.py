"""
HOOD Application & Window Manager
Governs process launching, controlled termination, window focus, and bounds management.
"""

from __future__ import annotations
import subprocess
import time
from typing import Optional, List, Dict, Any

from services.desktop.contracts import WindowInfo, WindowState
from services.desktop.windows_backend import WindowsNativeBackend


class ApplicationManager:
    """Safely manages application processes and their top-level windows."""

    def __init__(self, backend: Optional[WindowsNativeBackend] = None):
        self.backend = backend or WindowsNativeBackend()
        self.spawned_pids: List[int] = []

    def enumerate_windows(self) -> List[WindowInfo]:
        return self.backend.enumerate_windows()

    def find_window_by_title(self, title_substring: str) -> Optional[WindowInfo]:
        windows = self.backend.enumerate_windows()
        for w in windows:
            if title_substring.lower() in w.title.lower():
                return w
        return None

    def find_window_by_process(self, process_name: str) -> Optional[WindowInfo]:
        windows = self.backend.enumerate_windows()
        for w in windows:
            if process_name.lower() in w.process_name.lower():
                return w
        return None

    def focus_window(self, hwnd: int) -> bool:
        return self.backend.activate_window(hwnd)

    def launch_application(self, command: List[str]) -> Optional[int]:
        """Launches an approved local process and tracks its PID."""
        try:
            proc = subprocess.Popen(command)
            self.spawned_pids.append(proc.pid)
            return proc.pid
        except Exception:
            return None

    def close_window(self, hwnd: int) -> bool:
        return self.backend.close_window(hwnd)

    def terminate_process(self, pid: int) -> bool:
        """Controlled process termination."""
        try:
            import psutil
            if psutil.pid_exists(pid):
                p = psutil.Process(pid)
                p.terminate()
                p.wait(timeout=2)
                return True
        except Exception:
            pass
        return False

    def close_all_spawned(self):
        """Closes all applications launched during this session."""
        for pid in list(self.spawned_pids):
            self.terminate_process(pid)
        self.spawned_pids.clear()
