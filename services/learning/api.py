"""Owner-facing HTTP for self-learning. Recording a lesson is ordinary
operation; establishing a lesson as settled truth is Root-Owner only."""
from __future__ import annotations

import os
from pathlib import Path

from ui.routes import SERVICES, route

from .service import LearningService


def _svc() -> LearningService:
    svc = SERVICES.get("learning")
    if svc is None:
        from services.memory.service import MemoryService
        mem = SERVICES.get("memory") or MemoryService()
        svc = SERVICES.setdefault("learning", LearningService(
            mem, approval_service=SERVICES.get("approvals"),
            data_dir=Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "learning"))
    return svc


def _is_root_owner(ctx) -> bool:
    role = getattr(ctx.session, "role", None)
    return getattr(role, "value", role) == "ROOT_OWNER"


@route("GET", r"/api/learning/lessons", permission="VIEW_PROJECT_DATA")
def list_lessons(ctx):
    return {"lessons": _svc().list(ctx.username)}


@route("GET", r"/api/learning/pending", permission="VIEW_PROJECT_DATA")
def pending(ctx):
    return {"pending": _svc().pending_promotions(ctx.username)}


@route("POST", r"/api/learning/record", permission="CHAT_INTERACTION")
def record(ctx):
    ctx.require_confirm("record a lesson into governed memory")
    category = ctx.payload.get("category")
    lesson = ctx.payload.get("lesson")
    if not isinstance(category, str) or not isinstance(lesson, str) or not lesson.strip():
        raise ValueError("'category' and 'lesson' (non-empty strings) are required")
    return 201, _svc().record_outcome(ctx.username, category, lesson,
                                       evidence=str(ctx.payload.get("evidence", "")))


@route("POST", r"/api/learning/lessons/(?P<lesson_id>lesson_[0-9a-f]{16})/establish",
       permission="OWNERSHIP_ADMIN")
def establish(ctx):
    ctx.require_confirm("establish this lesson as settled truth HOOD may rely on")
    return _svc().establish(ctx.match["lesson_id"], approver=ctx.username, is_root_owner=_is_root_owner(ctx))
