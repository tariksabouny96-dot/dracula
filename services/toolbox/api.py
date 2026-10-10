"""Settings > Tools: what HOOD can install for missions, and the owner's permissions.

- Anyone who can run missions sees what a mission kind needs.
- Only the Root Owner allows installs (once per tool, with explicit confirmation), updates
  and removals. Installs happen only inside WSL2/Linux.
"""
from __future__ import annotations

from ui.routes import SERVICES, RouteConflict, ServiceUnavailable, route


def _toolbox():
    tb = SERVICES.get("toolbox")
    if tb is None:
        raise ServiceUnavailable("The toolbox isn't attached to this server")
    return tb


def _tool_list(payload, key="tools"):
    from .catalog import TOOLS
    tools = payload.get(key)
    if isinstance(tools, str):
        tools = [tools]
    if not isinstance(tools, list) or not tools or any(t not in TOOLS for t in tools):
        raise ValueError("Unknown tool; choose from: " + ", ".join(TOOLS))
    return tools


@route("GET", r"/api/tools", permission="EXECUTE_OBJECTIVE")
def tools_status(ctx):
    return _toolbox().status()


@route("GET", r"/api/tools/needs", permission="EXECUTE_OBJECTIVE")
def tools_needs(ctx):
    profile = (ctx.query.get("profile") or [""])[0]
    need = _toolbox().needs_for_profile(profile)
    return need or {"tools": [], "missing": [], "unapproved": [], "names": {}, "ready": True, "problem": None,
                    "running": None}


@route("GET", r"/api/tools/jobs/(?P<job_id>job_[0-9a-f]{12})", permission="EXECUTE_OBJECTIVE")
def tools_job(ctx):
    return _toolbox().job(ctx.match["job_id"])


@route("POST", r"/api/tools/install", permission="OWNERSHIP_ADMIN")
def tools_install(ctx):
    from .service import ToolUnavailable
    tools = _tool_list(ctx.payload)
    ctx.require_confirm("allow HOOD to install these tools inside WSL2 (once per tool; later updates "
                        "don't ask again)")
    try:
        return _toolbox().request_install(tools, ctx.username)
    except ToolUnavailable as exc:
        raise RouteConflict(str(exc)) from None


@route("POST", r"/api/tools/update", permission="OWNERSHIP_ADMIN")
def tools_update(ctx):
    from .service import ToolNotApproved, ToolUnavailable
    tool = _tool_list(ctx.payload, "tool")[0]
    try:
        return _toolbox().update(tool, ctx.username)       # already allowed once: no new confirmation
    except (ToolNotApproved, ToolUnavailable) as exc:
        raise RouteConflict(str(exc)) from None


@route("POST", r"/api/tools/remove", permission="OWNERSHIP_ADMIN")
def tools_remove(ctx):
    from .service import ToolUnavailable
    tool = _tool_list(ctx.payload, "tool")[0]
    ctx.require_confirm("remove this tool (HOOD will ask again before reinstalling it)")
    try:
        return _toolbox().remove(tool, ctx.username)
    except ToolUnavailable as exc:
        raise RouteConflict(str(exc)) from None
