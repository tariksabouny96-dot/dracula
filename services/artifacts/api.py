"""HTTP routes for the artifact registry (download tokens + verified download).

The server enforces Host, CSRF, session and permission before these run; every
handler is scoped to ``ctx.user_id``.
"""
from __future__ import annotations

import os
from pathlib import Path

from ui.routes import Raw, SERVICES, route

from .registry import ArtifactRegistry


def _registry() -> ArtifactRegistry:
    reg = SERVICES.get("artifacts")
    if reg is None:
        data_dir = Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "artifacts"
        reg = SERVICES.setdefault("artifacts", ArtifactRegistry(data_dir))
    return reg


@route("GET", r"/api/artifacts", permission="VIEW_PROJECT_DATA")
def list_artifacts(ctx):
    return {"artifacts": _registry().list(ctx.user_id)}


@route("GET", r"/api/artifacts/(?P<art_id>art_[0-9a-f]{32})", permission="VIEW_PROJECT_DATA")
def get_artifact(ctx):
    return _registry().get(ctx.user_id, ctx.match["art_id"])


@route("POST", r"/api/artifacts/(?P<art_id>art_[0-9a-f]{32})/token", permission="VIEW_PROJECT_DATA")
def mint_token(ctx):
    ttl = ctx.payload.get("ttl_seconds", 300)
    try:
        ttl = int(ttl)
    except (TypeError, ValueError):
        raise ValueError("ttl_seconds must be an integer")
    return 201, _registry().mint_token(ctx.user_id, ctx.match["art_id"], ttl)


@route("GET", r"/api/artifacts/download/(?P<token>[A-Za-z0-9_.=-]{16,4096})", permission="VIEW_PROJECT_DATA")
def download(ctx):
    # The token is bound to the authenticated owner; a token for anyone else is a 404.
    name, mime, data = _registry().redeem(ctx.match["token"], expected_owner=ctx.user_id)
    return Raw(body=data, content_type=mime, filename=name)
