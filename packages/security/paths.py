"""One canonical path-confinement check for file, upload, archive and workspace access."""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


class PathConfinementError(PermissionError):
    """Raised when a requested path is outside its approved root."""


def confine_path(root: Path, candidate: str, *, label: str = "path") -> Path:
    """Resolve ``candidate`` under ``root`` and refuse anything that escapes it.

    Rejects empty values, NUL bytes, foreign drive/UNC paths and backslash
    separators on POSIX (where ``C:\\x`` would otherwise be a relative name),
    ``..`` escapes, absolute paths outside ``root`` and symlinks resolving outside it.
    """
    if not isinstance(candidate, str) or not candidate.strip() or "\x00" in candidate:
        raise PathConfinementError(f"Invalid {label}")
    win = PureWindowsPath(candidate)
    if os.name != "nt" and (win.drive or "\\" in candidate):
        raise PathConfinementError(f"{label} '{candidate}' is outside approved workspace root")
    base = Path(root).resolve()
    resolved = (base / candidate).resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise PathConfinementError(f"{label} '{candidate}' is outside approved workspace root")
    return resolved
