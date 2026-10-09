"""
PROJECT SENTINEL — Core Data Contracts & Models
Defines vulnerability classifications, sentinel findings, firewall telemetry,
self-healing actions, and X red-team exercise scopes.
Governed by HOOD Master System Specification v1.3.
"""

from __future__ import annotations
from enum import Enum
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field


class FindingSeverity(str, Enum):
    INFORMATIONAL = "INFORMATIONAL"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingStatus(str, Enum):
    SUSPECTED = "SUSPECTED"
    CONFIRMED = "CONFIRMED"
    MITIGATED = "MITIGATED"
    PATCHED = "PATCHED"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class VulnerabilityCategory(str, Enum):
    AUTH_IDENTITY = "AUTH_IDENTITY"
    ACCESS_CONTROL = "ACCESS_CONTROL"
    INJECTION = "INJECTION"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    CSRF_SESSION = "CSRF_SESSION"
    INPUT_VALIDATION = "INPUT_VALIDATION"
    AI_PROMPT_INJECTION = "AI_PROMPT_INJECTION"
    AI_MEMORY_POISONING = "AI_MEMORY_POISONING"
    AI_TOOL_ABUSE = "AI_TOOL_ABUSE"
    SUPPLY_CHAIN_DEPENDENCY = "SUPPLY_CHAIN_DEPENDENCY"
    EXPOSED_INTERFACE = "EXPOSED_INTERFACE"
    FIREWALL_ANOMALY = "FIREWALL_ANOMALY"
    INTEGRITY_DRIFT = "INTEGRITY_DRIFT"
    UNKNOWN_NOVEL = "UNKNOWN_NOVEL"


class SentinelFinding(BaseModel):
    finding_id: str = Field(default_factory=lambda: f"VULN-{uuid.uuid4().hex[:8].upper()}")
    category: VulnerabilityCategory
    severity: FindingSeverity
    title: str
    affected_component: str
    description: str
    evidence: str
    reproduction_steps: Optional[str] = None
    proposed_mitigation: Optional[str] = None
    status: FindingStatus = FindingStatus.SUSPECTED
    discovered_by: str = "HOOD_SENTINEL"  # HOOD_SENTINEL, X_RED_TEAM, STATIC_ANALYZER, ZERO_DAY_LAB
    discovered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: Optional[str] = None
    owner_approval_required: bool = False
    residual_risk: Optional[str] = None


class FirewallProfileStatus(BaseModel):
    profile_name: str  # Domain, Private, Public
    is_active: bool
    state_raw: str


class FirewallRule(BaseModel):
    rule_name: str
    direction: str  # Inbound / Outbound
    action: str  # Allow / Block
    local_port: Optional[str] = None
    remote_port: Optional[str] = None
    protocol: Optional[str] = None
    program: Optional[str] = None
    is_enabled: bool = True


class FirewallTelemetry(BaseModel):
    is_active: bool = True
    profiles: List[FirewallProfileStatus] = Field(default_factory=list)
    listening_ports: List[Dict[str, Any]] = Field(default_factory=list)
    unexpected_exposed_ports: List[int] = Field(default_factory=list)
    baseline_deviations: List[str] = Field(default_factory=list)
    mode: str = "READ-ONLY"
    last_inspected: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SelfHealingAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"HEAL-{uuid.uuid4().hex[:8].upper()}")
    target_component: str
    description: str
    risk_level: str = "L1"  # L0 to L5
    is_authorized: bool = False
    executed: bool = False
    success: bool = False
    reverted: bool = False
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Optional[str] = None


class PatchCandidate(BaseModel):
    patch_id: str = Field(default_factory=lambda: f"PATCH-{uuid.uuid4().hex[:8].upper()}")
    finding_id: str
    affected_component: str
    root_cause: str
    remediation_summary: str
    patch_diff: str
    regression_tests_created: List[str] = Field(default_factory=list)
    layer1_developer_passed: bool = False
    layer2_x_red_team_passed: bool = False
    layer3_independent_validator_passed: bool = False
    approval_required: bool = True
    is_approved: bool = False
    is_deployed: bool = False
    deployed_at: Optional[str] = None
    rollback_ready: bool = True


class XExerciseScope(BaseModel):
    exercise_id: str = Field(default_factory=lambda: f"X-EX-{uuid.uuid4().hex[:8].upper()}")
    target: str  # Isolated mock target
    allowed_categories: List[VulnerabilityCategory]
    time_limit_seconds: int = 300
    resource_budget: str = "LOCAL_SANDBOX_ONLY"
    is_authorized_by_zack: bool = False
    activated_at: Optional[str] = None
    concluded_at: Optional[str] = None
    findings_generated: List[str] = Field(default_factory=list)
