"""Pluggable, authenticated HTTP routes for service modules.

Feature packages register endpoints here instead of editing ``ui/server.py``:

    from ui.routes import route, Raw

    @route("GET", r"/api/exports/(?P<export_id>exp_[0-9a-f]{32})", permission="VIEW_PROJECT_DATA")
    def get_export(ctx):
        return ctx.service("exports").get(ctx.user_id, ctx.match["export_id"])

Guarantees provided by the server before a handler runs (cannot be skipped by a module):
- Host allowlist, JSON-only bodies, CSRF on cookie POSTs, authenticated session.
- ``permission`` (a ``UserPermission`` name) checked against the live user record.
- ``EmergencyStopActive`` -> 423, ``KeyError`` -> 404, ``PermissionError`` -> 403,
  ``ValueError``/``TypeError`` -> 400, ``RouteConflict`` -> 409, ``ServiceUnavailable`` -> 503.
Handlers return a JSON-serialisable value, ``(status, value)``, or ``Raw(...)`` for files.
Every handler must scope data by ``ctx.user_id``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


class RouteConflict(RuntimeError):
    """Request is valid but conflicts with current state (HTTP 409)."""


class ServiceUnavailable(RuntimeError):
    """Feature not configured on this server (HTTP 503)."""


@dataclass
class Raw:
    body: bytes
    content_type: str = "application/octet-stream"
    filename: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    status: int = 200


@dataclass
class Stream:
    """Server-sent events. ``events`` yields (event_id, event_type, data) tuples; ``None`` means
    "nothing new" and lets the server send a heartbeat. The server closes the stream after
    ``max_seconds`` so clients reconnect with their last cursor."""
    events: Any
    max_seconds: int = 55
    heartbeat_seconds: int = 15


@dataclass
class Route:
    method: str
    pattern: re.Pattern
    permission: str
    handler: Callable[["RequestContext"], Any]


@dataclass
class RequestContext:
    session: Any
    payload: Dict[str, Any]
    match: Dict[str, str]
    query: Dict[str, List[str]]
    services: Dict[str, Any]

    @property
    def user_id(self) -> str:
        return self.session.user_id

    @property
    def username(self) -> str:
        return self.session.username

    def service(self, name: str) -> Any:
        svc = self.services.get(name)
        if svc is None:
            raise ServiceUnavailable(f"{name} service is not configured on this server")
        return svc

    def require_confirm(self, what: str) -> None:
        if self.payload.get("confirm") is not True:
            raise ValueError(f"Explicit confirmation required: {what}")


ROUTES: List[Route] = []
# Long-lived service objects, filled by the runtime/server at start-up (name -> instance).
SERVICES: Dict[str, Any] = {}


def route(method: str, pattern: str, permission: str):
    if method not in ("GET", "POST"):
        raise ValueError("Only GET and POST are routed")

    def deco(fn):
        ROUTES.append(Route(method, re.compile(pattern), permission, fn))
        return fn
    return deco


def find(method: str, path: str):
    for r in ROUTES:
        if r.method == method:
            m = r.pattern.fullmatch(path)
            if m:
                return r, m.groupdict()
    return None, None


def load_modules() -> List[str]:
    """Import every registered feature API module (each registers its routes on import)."""
    import importlib
    loaded = []
    for name in API_MODULES:
        try:
            importlib.import_module(name)
            loaded.append(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
    return loaded


# Feature API modules. Append new modules here (one line each).
API_MODULES: List[str] = [
    "services.console.api",
    "services.exports.api",
    "services.firewall.api",
    "services.evolution.self_development_api",
    "services.learning.api",
    "services.settings.api",
    "services.voice.api",
]
