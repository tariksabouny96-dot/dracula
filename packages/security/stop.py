"""Process-wide emergency stop latch checked at every execution boundary.

The latch is engaged by the emergency stop controller and checked by the tool
gateway, grant issuance and the agent engine before any side effect. When a
``state_file`` is given the engaged state survives a process restart, so a
crash after a stop cannot silently resume work.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class EmergencyStopActive(RuntimeError):
    """Raised when work is attempted while the emergency stop is engaged."""


class StopLatch:
    def __init__(self, state_file: Optional[Path] = None):
        self._lock = threading.Lock()
        self._engaged = False
        self._reason = ""
        self._engaged_at: Optional[str] = None
        self.state_file = Path(state_file) if state_file else None
        if self.state_file and self.state_file.is_file():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._engaged = bool(data.get("engaged"))
                self._reason = str(data.get("reason", ""))
                self._engaged_at = data.get("engaged_at")
            except (OSError, ValueError):
                # Unreadable stop state fails closed: treat as engaged.
                self._engaged, self._reason = True, "Unreadable stop state file"

    def _persist(self):
        if not self.state_file:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"engaged": self._engaged, "reason": self._reason,
                                   "engaged_at": self._engaged_at}), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def engage(self, reason: str) -> None:
        with self._lock:
            self._engaged = True
            self._reason = reason
            self._engaged_at = datetime.now(timezone.utc).isoformat()
            self._persist()

    def release(self) -> None:
        with self._lock:
            self._engaged = False
            self._reason = ""
            self._engaged_at = None
            self._persist()

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def reason(self) -> str:
        return self._reason

    def check(self) -> None:
        if self._engaged:
            raise EmergencyStopActive(f"Emergency stop is active: {self._reason}")

    def snapshot(self) -> dict:
        return {"engaged": self._engaged, "reason": self._reason, "engaged_at": self._engaged_at}
