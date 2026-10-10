"""Owner-facing HTTP for governed self-development.

Proposing records a change and opens an owner approval; it never writes code.
Applying requires the Root Owner and the matching approval, and rolls back on
any validation failure. Guardrail files are refused outright.
"""
from __future__ import annotations

import os
from pathlib import Path

from ui.routes import SERVICES, route

from .self_development import SelfDevelopmentController


def _controller() -> SelfDevelopmentController:
    c = SERVICES.get("selfdev")
    if c is None:
        c = SERVICES.setdefault("selfdev", SelfDevelopmentController(
            workspace_root=Path.cwd(),
            approval_service=SERVICES.get("approvals"),
            data_dir=Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "self_dev"))
    return c


def _is_root_owner(ctx) -> bool:
    role = getattr(ctx.session, "role", None)
    return getattr(role, "value", role) == "ROOT_OWNER"


@route("GET", r"/api/selfdev/proposals", permission="VIEW_PROJECT_DATA")
def list_proposals(ctx):
    return {"proposals": _controller().list()}


@route("GET", r"/api/selfdev/proposals/(?P<pid>sd_[0-9a-f]{24})", permission="VIEW_PROJECT_DATA")
def get_proposal(ctx):
    return _controller().get(ctx.match["pid"])


@route("POST", r"/api/selfdev/propose", permission="MODIFY_PROJECT_CODE")
def propose(ctx):
    ctx.require_confirm("record a self-development proposal for owner approval")
    target = ctx.payload.get("target_path")
    content = ctx.payload.get("content")
    rationale = ctx.payload.get("rationale", "")
    if not isinstance(target, str) or not isinstance(content, str):
        raise ValueError("'target_path' and 'content' (strings) are required")
    return 201, _controller().propose(target, content, str(rationale), proposed_by=ctx.username)


@route("POST", r"/api/selfdev/proposals/(?P<pid>sd_[0-9a-f]{24})/apply", permission="OWNERSHIP_ADMIN")
def apply_proposal(ctx):
    ctx.require_confirm("apply a self-development change to HOOD's own code")
    return _controller().apply(ctx.match["pid"], approver=ctx.username, is_root_owner=_is_root_owner(ctx))
