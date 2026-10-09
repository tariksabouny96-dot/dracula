"""
HOOD Core Contracts and Data Models
Authoritative Entity Definitions according to Master System Specification Appendix B & Section 4.4.
"""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field


# --- Enums ---

class RiskLevel(str, Enum):
    L0 = "L0"  # Observe / read / public lookup - Automatic
    L1 = "L1"  # Safe reversible local action - Automatic
    L2 = "L2"  # Controlled system modification - Automatic only when covered by policy
    L3 = "L3"  # External / prod / consequential action - Usually approval-gated
    L4 = "L4"  # Security, money, credentials, production data - Explicit approval required
    L5 = "L5"  # Irreversible / extreme blast radius - Explicit approval + checkpoint + confirmation


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EMERGENCY_STOPPED = "EMERGENCY_STOPPED"


class SystemState(str, Enum):
    IDLE = "IDLE"
    THINKING = "THINKING"
    WORKING = "WORKING"
    WAITING = "WAITING"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DEGRADED = "DEGRADED"
    SECURITY_HOLD = "SECURITY_HOLD"
    FINANCIAL_HOLD = "FINANCIAL_HOLD"
    RECOVERY = "RECOVERY"
    X_ACTIVE = "X_ACTIVE"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class MemoryType(str, Enum):
    WORKING = "WORKING"
    PERSONAL = "PERSONAL"
    PROJECT = "PROJECT"
    SEMANTIC = "SEMANTIC"
    EPISODIC = "EPISODIC"
    PROCEDURAL = "PROCEDURAL"
    DECISION = "DECISION"
    EXPERIENCE = "EXPERIENCE"
    GOVERNANCE = "GOVERNANCE"
    X_SEALED = "X_SEALED"


class LearningStatus(str, Enum):
    OBSERVATION = "OBSERVATION"
    CANDIDATE = "CANDIDATE"
    PROVISIONAL = "PROVISIONAL"
    ESTABLISHED = "ESTABLISHED"
    SUPERSEDED = "SUPERSEDED"


class ModelClass(str, Enum):
    FAST = "FAST"
    STANDARD = "STANDARD"
    DEEP = "DEEP"
    SPECIALIST = "SPECIALIST"
    LOCAL_PRIVATE = "LOCAL_PRIVATE"


class ProviderName(str, Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"
    MOCK = "mock"


# --- Core Entities ---

class SecretReference(BaseModel):
    uri: str = Field(description="Format: SECRET://provider/key")
    provider: str
    key_name: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, provider: str, key_name: str, description: Optional[str] = None) -> SecretReference:
        return cls(uri=f"SECRET://{provider}/{key_name}", provider=provider, key_name=key_name, description=description)


class EvidencePacket(BaseModel):
    evidence_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim: str
    source: str
    source_type: str  # e.g., 'primary_api', 'web_page', 'file', 'verified_tool'
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_primary: bool = False
    freshness_sec: int = 0
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    conflicts: List[str] = Field(default_factory=list)
    extract: str = ""
    verifying_agent: str = "Hood"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapabilityGrant(BaseModel):
    grant_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    capability: str  # e.g., 'fs:read', 'fs:write', 'shell:exec', 'model:call'
    target_scope: str  # e.g., 'workspace_root', 'project_tmp'
    granted_to: str  # agent or tool name
    expires_at: datetime
    is_revoked: bool = False

    def is_valid(self) -> bool:
        return not self.is_revoked and datetime.now(timezone.utc) < self.expires_at


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    action_type: str
    target: str
    risk_level: RiskLevel
    reason: str
    options: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_option: str
    checkpoint_ref: Optional[str] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    # Who the approval authorizes (agent/actor id); None = legacy unbound request.
    principal: Optional[str] = None
    # Approvals are never open-ended; an unresolved or unused approval expires.
    expires_at: Optional[datetime] = None


class AgentResponseContract(BaseModel):
    task_id: str
    parent_task: Optional[str] = None
    agent: str
    objective: str
    inputs: Dict[str, Any] = Field(default_factory=dict)
    context_used: List[str] = Field(default_factory=list)
    result: Any = None
    evidence: List[EvidencePacket] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    risks: List[str] = Field(default_factory=list)
    unknowns: List[str] = Field(default_factory=list)
    alternatives: List[Dict[str, Any]] = Field(default_factory=list)
    recommendation: str = ""
    artifacts: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    cost: float = 0.0
    execution_time_ms: int = 0
    followup: List[str] = Field(default_factory=list)


class MemoryObject(BaseModel):
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: MemoryType
    content: str
    project: str = "default"
    source: str = "system"
    source_agent: str = "Hood"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_until: Optional[datetime] = None
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    evidence: List[str] = Field(default_factory=list)
    verification_status: str = "UNVERIFIED"
    sensitivity: str = "INTERNAL"
    access_policy: str = "PROJECT_ISOLATED"
    version: int = 1
    supersedes: Optional[str] = None
    related_memories: List[str] = Field(default_factory=list)
    learning_status: LearningStatus = LearningStatus.OBSERVATION


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str
    task_id: Optional[str] = None
    project: str = "default"
    action: str
    target: str
    policy_decision: str  # ALLOW, DENY, REQUIRE_APPROVAL
    approval_ref: Optional[str] = None
    input_refs: List[str] = Field(default_factory=list)
    tool_or_model: Optional[str] = None
    cost: float = 0.0
    result: str = ""
    artifact_refs: List[str] = Field(default_factory=list)
    verification: str = "PASSED"
    error: Optional[str] = None
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ModelUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class ModelRequest(BaseModel):
    model_class: ModelClass = ModelClass.STANDARD
    prompt: str
    system_prompt: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.7
    allowed_providers: Optional[List[ProviderName]] = None
    preferred_provider: Optional[ProviderName] = None
    task_id: Optional[str] = None
    agent: Optional[str] = None
    project: str = "default"
    # e.g. "application/json": ask the provider for structured output (still validated by Hood).
    response_mime_type: Optional[str] = None
    # JSON Schema the provider should constrain decoding to (output is still validated by Hood).
    response_schema: Optional[Dict[str, Any]] = None


class ModelResponse(BaseModel):
    text: str
    provider: ProviderName
    model_name: str
    usage: ModelUsage
    latency_ms: int
    is_mock: bool = False
    is_fallback: bool = False


class TaskNode(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_task_id: Optional[str] = None
    title: str
    objective: str
    project: str = "default"
    assigned_agent: str = "Hood"
    dependencies: List[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    risk_level: RiskLevel = RiskLevel.L1
    inputs: Dict[str, Any] = Field(default_factory=dict)
    response: Optional[AgentResponseContract] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class XScopeVersion(BaseModel):
    engagement_id: str
    version: int = 1
    authorization_confirmed: bool = False
    authorized_by: str = ""
    authorization_reference: str = ""
    in_scope: List[str] = Field(default_factory=list)
    out_of_scope: List[str] = Field(default_factory=list)
    recon: bool = False
    scanning: bool = False
    exploitation: bool = False
    exploit_development: bool = False
    auth_testing: bool = False
    privilege_escalation: bool = False
    vulnerability_chaining: bool = False
    proof_of_impact: bool = False
    destructive_testing: bool = False
    denial_of_service: bool = False
    real_customer_data_access: bool = False
    status: str = "CLOSED"  # ACTIVE or CLOSED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
