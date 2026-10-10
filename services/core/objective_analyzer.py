"""
HOOD Objective Classifier and Constraint Extractor
Extracts user intent, operational constraints, required domain leads, and execution modes.
Governed by Master System Specification Sections 2, 4, 10 & Live Operational Directives.
"""

from __future__ import annotations
import re
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class ObjectiveCategory(str, Enum):
    READ_ONLY_INSPECTION = "READ_ONLY_INSPECTION"
    RESEARCH = "RESEARCH"
    PLANNING = "PLANNING"
    SOFTWARE_DEVELOPMENT = "SOFTWARE_DEVELOPMENT"
    CYBERSECURITY_REVIEW = "CYBERSECURITY_REVIEW"
    COMMERCE_BUSINESS = "COMMERCE_BUSINESS"
    DATA_ANALYSIS = "DATA_ANALYSIS"
    OPERATIONS = "OPERATIONS"
    BROWSER_TASK = "BROWSER_TASK"
    DESKTOP_TASK = "DESKTOP_TASK"
    SYSTEM_DIAGNOSTIC = "SYSTEM_DIAGNOSTIC"
    MODEL_INFRASTRUCTURE = "MODEL_INFRASTRUCTURE"
    MULTI_DOMAIN = "MULTI_DOMAIN"


class ExtractedConstraints(BaseModel):
    read_only: bool = False
    max_incremental_cost_usd: float = 0.0
    x_allowed: bool = False
    execution_scope: str = "LOCAL"  # LOCAL, EXTERNAL, HYBRID
    external_communication_allowed: bool = False
    action_mode: str = "EXECUTE"  # EXECUTE, ADVISORY, PLAN_ONLY
    raw_constraints_found: List[str] = Field(default_factory=list)


class ParsedObjective(BaseModel):
    original_prompt: str
    category: ObjectiveCategory
    primary_domains: List[str] = Field(default_factory=list)
    supporting_domains: List[str] = Field(default_factory=list)
    constraints: ExtractedConstraints
    required_tools: List[str] = Field(default_factory=list)
    description: str = ""


class ObjectiveAnalyzer:
    """Parses natural language objectives into structured intents and enforceable constraints."""

    READ_ONLY_PATTERNS = [
        r"\bdo not modify\b", r"\bdon'?t modify\b", r"\bread[- ]only\b",
        r"\bno changes\b", r"\bwithout modifying\b", r"\bdo not edit\b",
        r"\bno file modification\b", r"\binspect only\b"
    ]

    NO_SPEND_PATTERNS = [
        r"\bwithout spending money\b", r"\bdo not spend\b", r"\bzero[- ]cost\b",
        r"\b\$0\b", r"\bno cost\b", r"\bfree only\b"
    ]

    NO_X_PATTERNS = [
        r"\bdo not activate x\b", r"\bdon'?t activate x\b", r"\bno x\b",
        r"\bx dormant\b", r"\bwithout x\b", r"\bdo not wake x\b"
    ]

    ADVISORY_PATTERNS = [
        r"\bpropose\b", r"\brecommendations? only\b", r"\bplan only\b",
        r"\badvise\b", r"\bdo not execute\b"
    ]

    NO_EXTERNAL_COMMS = [
        r"\bdo not contact\b", r"\bdon'?t contact anyone\b",
        r"\bno external action\b", r"\bno external communication\b"
    ]

    @classmethod
    def extract_constraints(cls, text: str) -> ExtractedConstraints:
        t = text.lower()
        found = []

        read_only = any(re.search(p, t) for p in cls.READ_ONLY_PATTERNS)
        if read_only:
            found.append("READ_ONLY=TRUE")

        zero_cost = any(re.search(p, t) for p in cls.NO_SPEND_PATTERNS)
        if zero_cost or "money" in t:
            found.append("MAX_INCREMENTAL_COST=$0.00")

        no_x = any(re.search(p, t) for p in cls.NO_X_PATTERNS)
        if no_x or "x" in t:
            found.append("X_ALLOWED=FALSE")

        advisory = any(re.search(p, t) for p in cls.ADVISORY_PATTERNS)
        if advisory:
            found.append("ACTION_MODE=ADVISORY")

        no_comms = any(re.search(p, t) for p in cls.NO_EXTERNAL_COMMS)
        if no_comms:
            found.append("EXTERNAL_COMMUNICATION=FALSE")

        # Explicit X authorization detection
        x_authorized = bool(re.search(r"\bauthorize x\b|\bwake x\b|\bx active\b", t)) and not no_x

        return ExtractedConstraints(
            read_only=read_only,
            max_incremental_cost_usd=0.0,  # Always enforced default under constitutional rule
            x_allowed=x_authorized,
            execution_scope="LOCAL" if "local" in t or read_only else "HYBRID",
            external_communication_allowed=not no_comms,
            action_mode="ADVISORY" if advisory or read_only else "EXECUTE",
            raw_constraints_found=found
        )

    @classmethod
    def analyze(cls, prompt: str) -> ParsedObjective:
        p_lower = prompt.lower()
        constraints = cls.extract_constraints(prompt)

        # 1. System Self-Assessment & Diagnostic
        if any(k in p_lower for k in ["inspect your", "system-status", "environment, hardware", "status assessment", "capabilities", "inspect my current hood environment", "inspect my environment", "highest-value improvements", "high-value improvements"]):
            category = ObjectiveCategory.SYSTEM_DIAGNOSTIC
            primary = ["Operations_Lead", "Engineering_Lead"]
            supporting = ["Cybersecurity_Lead"]
            tools = ["system_diagnostics", "fs_list_dir"]
            desc = "Full read-only environment, hardware, provider, and tool assessment."

        # 2. Research & Intelligence
        elif any(k in p_lower for k in ["research", "internet intelligence", "sources", "find articles"]):
            category = ObjectiveCategory.RESEARCH
            primary = ["Research_Lead"]
            supporting = ["Data_Lead"]
            tools = ["internet_intelligence"]
            desc = "Evidence-backed external technical and web intelligence synthesis."

        # 3. Cybersecurity Review
        elif any(k in p_lower for k in ["security review", "vulnerability", "audit security", "isolation review", "appsec"]):
            category = ObjectiveCategory.CYBERSECURITY_REVIEW
            primary = ["Cybersecurity_Lead"]
            supporting = ["Engineering_Lead"]
            tools = ["vault_inspector", "fs_read_file"]
            desc = "Security boundaries, secrets isolation, and threat review."

        # 4. Commerce & Business Analysis
        elif any(k in p_lower for k in ["e-commerce", "commerce", "product opportunity", "unit economics", "business analysis"]):
            category = ObjectiveCategory.COMMERCE_BUSINESS
            primary = ["Commerce_Lead", "Research_Lead"]
            supporting = ["Data_Lead", "Finance_Lead"]
            tools = ["market_analyzer"]
            desc = "E-commerce opportunity, demand, unit economics, and risk evaluation."

        # 5. Software Development Plan Only
        elif ("plan" in p_lower or "propose" in p_lower) and constraints.read_only:
            category = ObjectiveCategory.PLANNING
            primary = ["Engineering_Lead"]
            supporting = ["Cybersecurity_Lead"]
            tools = ["fs_list_dir", "git_ops"]
            desc = "Repository inspection and engineering prioritization without code modification."

        # 6. Software Development Break/Fix Execution
        elif any(k in p_lower for k in ["fix", "build", "implement", "refactor", "repair"]) and not constraints.read_only:
            category = ObjectiveCategory.SOFTWARE_DEVELOPMENT
            primary = ["Engineering_Lead"]
            supporting = ["Cybersecurity_Lead"]
            tools = ["dev_inspect", "dev_test", "dev_apply_code_change", "dev_rollback"]
            desc = "Reversible development lifecycle: inspect, test, patch, verify, and rollback on failure."

        # Default Multi-domain
        else:
            category = ObjectiveCategory.MULTI_DOMAIN
            primary = ["Operations_Lead", "Engineering_Lead"]
            supporting = ["Cybersecurity_Lead"]
            tools = ["fs_list_dir"]
            desc = "General multi-domain objective."

        return ParsedObjective(
            original_prompt=prompt,
            category=category,
            primary_domains=primary,
            supporting_domains=supporting,
            constraints=constraints,
            required_tools=tools,
            description=desc
        )
