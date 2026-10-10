"""HTTP routes for document exports, plus the capability state provider.

The server enforces Host, CSRF, session and permission before these run.
Creating exports is a side effect, so it requires EXECUTE_OBJECTIVE and an
explicit confirm; reads require VIEW_PROJECT_DATA and are scoped to the caller.
"""
from __future__ import annotations

import os
from pathlib import Path

from services.capabilities.registry import register_state_provider
from ui.routes import SERVICES, route

from .service import ExportService

# Import the artifacts API so its routes register whenever exports is loaded.
from services.artifacts import api as _artifacts_api  # noqa: F401

_STATE_CACHE = {"at": 0.0, "value": None}
_STATE_TTL = 600


def _service() -> ExportService:
    svc = SERVICES.get("exports")
    if svc is None:
        data_dir = Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "exports"
        latch = getattr(SERVICES.get("emergency_stop"), "stop_latch", None)
        svc = SERVICES.setdefault("exports", ExportService(data_dir, stop_latch=latch))
    return svc


@route("POST", r"/api/exports", permission="EXECUTE_OBJECTIVE")
def create_export(ctx):
    ctx.require_confirm("render and store document artifacts")
    spec = ctx.payload.get("spec")
    formats = ctx.payload.get("formats")
    if not isinstance(spec, dict):
        raise ValueError("'spec' object is required")
    if not isinstance(formats, list) or not formats:
        raise ValueError("'formats' must be a non-empty list")
    record = _service().export(ctx.user_id, spec, [str(f) for f in formats])
    status = 201 if record["status"] in ("complete", "partial") else 422
    return status, record


@route("GET", r"/api/exports", permission="VIEW_PROJECT_DATA")
def list_exports(ctx):
    return {"exports": _service().list(ctx.user_id)}


@route("GET", r"/api/exports/(?P<exp_id>exp_[0-9a-f]{32})", permission="VIEW_PROJECT_DATA")
def get_export(ctx):
    return _service().get(ctx.user_id, ctx.match["exp_id"])


def _capability_state():
    import time
    now = time.time()
    if _STATE_CACHE["value"] is not None and now - _STATE_CACHE["at"] < _STATE_TTL:
        return _STATE_CACHE["value"]
    try:
        results = _service().self_test()
    except Exception as exc:
        value = {"state": "failed", "detail": f"self-test error: {type(exc).__name__}"}
    else:
        ok = [f for f, r in results.items() if r == "ok"]
        if len(ok) == len(results):
            value = {"state": "available", "detail": "formats: " + ", ".join(sorted(ok))}
        elif ok:
            value = {"state": "degraded", "detail": "working: " + ", ".join(sorted(ok))}
        else:
            value = {"state": "failed", "detail": "no format renders and validates"}
    _STATE_CACHE.update(at=now, value=value)
    return value


register_state_provider("artifacts", _capability_state)
