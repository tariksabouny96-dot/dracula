"""Typed, versioned contracts for the Hood multi-agent engine (schema v1).

Every object crossing a trust boundary (model output -> engine, engine ->
store, store -> UI) is one of these models. Model output is parsed into them
with ``extra='forbid'`` and explicit size limits; anything else is rejected.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

MAX_TASKS = 12
MAX_DEPTH = 6
MAX_FANOUT = 6
MAX_FILES_PER_CHANGE = 24
MAX_FILE_BYTES = 64 * 1024
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


class MissionState(str, Enum):
    PLANNING = "PLANNING"
    AWAITING_PLAN_APPROVAL = "AWAITING_PLAN_APPROVAL"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"          # independent verification passed, artifact hashed
    FAILED = "FAILED"                # verification failed after the repair budget
    UNVERIFIED = "UNVERIFIED"        # work ran but could not be independently checked
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"              # needs an operator decision (crash, budget, stop)


TERMINAL_MISSION_STATES = {MissionState.COMPLETED, MissionState.FAILED, MissionState.UNVERIFIED,
                           MissionState.CANCELLED}


class TaskState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class AgentRole(str, Enum):
    ENGINEER = "engineer"      # writes application source and its own unit tests
    QA = "qa"                  # writes independent acceptance tests (qa_tests/ only)
    REVIEWER = "reviewer"      # reads the workspace and reports findings (no writes)


# Which workspace sub-trees each role may write. Enforced by the sandbox, not the prompt.
ROLE_WRITE_ROOTS = {
    AgentRole.ENGINEER: ("app", "tests"),
    AgentRole.QA: ("qa_tests",),
    AgentRole.REVIEWER: (),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlannedTask(_Strict):
    id: str
    role: AgentRole
    title: str = Field(min_length=3, max_length=120)
    instructions: str = Field(min_length=10, max_length=4000)
    depends_on: List[str] = Field(default_factory=list, max_length=MAX_FANOUT)

    @field_validator("id")
    @classmethod
    def _id(cls, value):
        if not TASK_ID_RE.fullmatch(value):
            raise ValueError("task id must match ^[a-z][a-z0-9_]{1,39}$")
        return value


class MissionPlan(_Strict):
    summary: str = Field(min_length=10, max_length=2000)
    deliverable: str = Field(min_length=3, max_length=400)
    # Exact entry points both the engineer (implements) and QA (tests) must use. Shared
    # contract keeps their work independent but compatible.
    interface_contract: str = Field(default="", max_length=3000)
    tasks: List[PlannedTask] = Field(min_length=1, max_length=MAX_TASKS)
    clarifications_needed: List[str] = Field(default_factory=list, max_length=8)


class FileWrite(_Strict):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=MAX_FILE_BYTES)


class AgentWorkProduct(_Strict):
    """Structured output of an engineer or QA agent: files to write, plus honesty fields."""
    files: List[FileWrite] = Field(default_factory=list, max_length=MAX_FILES_PER_CHANGE)
    notes: str = Field(default="", max_length=4000)
    uncertainty: str = Field(default="", max_length=2000)


class ReviewFinding(_Strict):
    severity: str = Field(pattern=r"^(info|low|medium|high|critical)$")
    path: str = Field(default="", max_length=200)
    message: str = Field(min_length=3, max_length=1000)


class ReviewReport(_Strict):
    findings: List[ReviewFinding] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=4000)


class CheckResult(_Strict):
    name: str
    command: List[str]
    exit_code: Optional[int]
    passed: bool
    output_tail: str = ""
    duration_ms: int = 0
    tests_collected: Optional[int] = None
    # True when the suite itself could not be collected (syntax/import error in the tests),
    # which says nothing about the application.
    suite_invalid: bool = False


class VerificationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"


class VerificationDecision(_Strict):
    verdict: VerificationVerdict
    checks: List[CheckResult]
    workspace_sha256: str
    reason: str = ""


# JSON Schemas handed to providers that support constrained decoding. They mirror the
# pydantic models above; Hood still validates every response with those models.
_STR = {"type": "string"}
PLAN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": _STR, "deliverable": _STR, "interface_contract": _STR,
        "tasks": {"type": "array", "items": {"type": "object", "properties": {
            "id": _STR, "role": {"type": "string", "enum": ["engineer", "qa", "reviewer"]},
            "title": _STR, "instructions": _STR, "depends_on": {"type": "array", "items": _STR}},
            "required": ["id", "role", "title", "instructions", "depends_on"]}},
        "clarifications_needed": {"type": "array", "items": _STR}},
    "required": ["summary", "deliverable", "interface_contract", "tasks", "clarifications_needed"],
}
WORK_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "files": {"type": "array", "items": {"type": "object", "properties": {"path": _STR, "content": _STR},
                                             "required": ["path", "content"]}},
        "notes": _STR, "uncertainty": _STR},
    "required": ["files", "notes", "uncertainty"],
}
REVIEW_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
            "path": _STR, "message": _STR}, "required": ["severity", "path", "message"]}},
        "notes": _STR},
    "required": ["findings", "notes"],
}
