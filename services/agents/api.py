"""Mission workspace view, "Run on my PC" approval and Settings > Agents.

- Files: the owner sees what the agents wrote while they work (read-only, size-limited).
- Run on my PC: off by default (Root Owner setting). On a host without a sandbox, a
  Python mission stops before its checks; the owner approves running the fixed
  commands on this computer for the exact files shown (bound to their hash).
- Open folder: shows the mission folder in this computer's file manager.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from ui.routes import SERVICES, RouteConflict, ServiceUnavailable, route

MISSION = r"/api/agents/missions/(?P<mission_id>agm_[0-9a-f]{32})"


def _engine():
    engine = SERVICES.get("agents")
    if engine is None:
        raise ServiceUnavailable("Agent engine not configured on this server")
    return engine


def _conflicts(fn):
    from services.agents.engine import MissionConflict

    def wrapped(ctx):
        try:
            return fn(ctx)
        except MissionConflict as exc:
            raise RouteConflict(str(exc)) from None
    wrapped.__name__ = fn.__name__
    return wrapped


@route("GET", MISSION + r"/files", permission="EXECUTE_OBJECTIVE")
def mission_files(ctx):
    return _engine().files(ctx.user_id, ctx.match["mission_id"])


@route("GET", MISSION + r"/file", permission="EXECUTE_OBJECTIVE")
def mission_file(ctx):
    path = (ctx.query.get("path") or [""])[0]
    if not path:
        raise ValueError("path is required")
    return _engine().read_file(ctx.user_id, ctx.match["mission_id"], path)


@route("POST", MISSION + r"/approve_local_run", permission="OWNERSHIP_ADMIN")
@_conflicts
def approve_local_run(ctx):
    ctx.require_confirm("run the mission's checks directly on this computer, without isolation")
    return _engine().approve_local_run(ctx.user_id, ctx.match["mission_id"], ctx.payload.get("workspace_sha256"),
                                       ctx.username)


def _open_in_file_manager(path: str) -> str:
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - engine-owned folder path, owner's own request
        return "explorer"
    if sys.platform == "darwin":
        subprocess.Popen(["open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "finder"
    if "microsoft" in platform.release().lower() and shutil.which("explorer.exe") and shutil.which("wslpath"):
        win = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, timeout=10).stdout.strip()
        subprocess.Popen(["explorer.exe", win], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "explorer (WSL)"
    if shutil.which("xdg-open") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "file manager"
    raise RouteConflict(f"No file manager is available where HOOD runs. The folder is: {path}")


@route("POST", MISSION + r"/open_folder", permission="OWNERSHIP_ADMIN")
def open_folder(ctx):
    ctx.require_confirm("open the mission folder in this computer's file manager")
    folder = _engine().files(ctx.user_id, ctx.match["mission_id"])["folder"]
    return {"opened_with": _open_in_file_manager(folder), "folder": folder}


@route("GET", r"/api/settings/agents", permission="OWNERSHIP_ADMIN")
def get_agent_settings(ctx):
    return {**_engine().local_run_settings(), "platform": sys.platform}


@route("POST", r"/api/settings/agents", permission="OWNERSHIP_ADMIN")
def set_agent_settings(ctx):
    enabled = ctx.payload.get("allow_local_run")
    if enabled is True:
        ctx.require_confirm("allow agent-written code checks to run directly on this computer (each mission "
                            "still asks you first)")
    elif enabled is False:
        ctx.require_confirm("turn off \"Run on my PC\"")
    return {**_engine().set_local_run(enabled, ctx.username), "platform": sys.platform}
