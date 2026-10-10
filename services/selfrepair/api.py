"""Self-repair over HTTP. Root Owner only: reporting sends excerpts of HOOD's code (and, with consent,
a screenshot) to the AI provider, and applying changes HOOD's code. Every change needs explicit
confirmation; applying is bound to the exact fix shown (its hash)."""
from __future__ import annotations

from ui.routes import SERVICES, Raw, RouteConflict, ServiceUnavailable, route

from .service import SelfRepairConflict, SelfRepairError

RID = r"(?P<rid>sr_[0-9a-f]{12})"


def _svc():
    svc = SERVICES.get("selfrepair")
    if svc is None:
        raise ServiceUnavailable("Self-repair isn't attached to this server")
    return svc


def _is_root(ctx) -> bool:
    role = getattr(ctx.session, "role", None)
    return getattr(role, "value", role) == "ROOT_OWNER"


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (SelfRepairConflict, SelfRepairError) as exc:
        raise RouteConflict(str(exc)) from None


@route("GET", r"/api/selfrepair", permission="OWNERSHIP_ADMIN")
def list_reports(ctx):
    return {"reports": _svc().list()}


@route("GET", r"/api/selfrepair/" + RID, permission="OWNERSHIP_ADMIN")
def get_report(ctx):
    return _svc().get(ctx.match["rid"])


@route("GET", r"/api/selfrepair/" + RID + r"/screenshot", permission="OWNERSHIP_ADMIN")
def get_screenshot(ctx):
    data, mime = _svc().screenshot(ctx.match["rid"])
    return Raw(data, content_type=mime, headers={"Cache-Control": "no-store"})


@route("POST", r"/api/selfrepair/report", permission="OWNERSHIP_ADMIN")
def report(ctx):
    ctx.require_confirm("send this report and excerpts of HOOD's code to the AI provider to look for a fix")
    p = ctx.payload
    return 201, _call(_svc().report, ctx.username, p.get("text", ""), p.get("screenshot_b64") or None,
                      p.get("screenshot_mime") or None, p.get("screenshot_consent") is True)


@route("POST", r"/api/selfrepair/" + RID + r"/apply", permission="OWNERSHIP_ADMIN")
def apply(ctx):
    ctx.require_confirm("change HOOD's code with this exact fix (a restore point is kept; you can undo it)")
    return _call(_svc().apply, ctx.match["rid"], ctx.payload.get("proposal_sha"), ctx.username, _is_root(ctx))


@route("POST", r"/api/selfrepair/" + RID + r"/undo", permission="OWNERSHIP_ADMIN")
def undo(ctx):
    ctx.require_confirm("put back HOOD's code as it was before this fix")
    return _call(_svc().undo, ctx.match["rid"], ctx.username, _is_root(ctx))


@route("POST", r"/api/selfrepair/" + RID + r"/discard", permission="OWNERSHIP_ADMIN")
def discard(ctx):
    return _call(_svc().discard, ctx.match["rid"], ctx.username)


@route("POST", r"/api/selfrepair/" + RID + r"/run_checks_here", permission="OWNERSHIP_ADMIN")
def run_checks_here(ctx):
    ctx.require_confirm("run HOOD's tests with this fix directly on this computer (no sandbox isolation)")
    return _call(_svc().approve_local_run, ctx.match["rid"], ctx.username)


@route("POST", r"/api/selfrepair/restart", permission="OWNERSHIP_ADMIN")
def restart(ctx):
    ctx.require_confirm("restart HOOD now so the fix takes effect")
    if not _is_root(ctx):
        raise PermissionError("Only the Root Owner can restart HOOD")
    return {"message": _call(_svc().restart, ctx.username)}
