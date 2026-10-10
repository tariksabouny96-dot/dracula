"""
HOOD Multi-Node Architecture - Contracts & Identity Schemas
Cryptographic node identity, capability advertisement, and distributed task envelopes.
Governed by Master System Specification Section 2, 9, 14 & V0.5A Multi-Node Spec.
"""

from enum import Enum
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import uuid
import hashlib
import json
import base64
from cryptography.hazmat.primitives.asymmetric import ed25519


class NodeType(str, Enum):
    CLOUD_CORE = "CLOUD_CORE"
    LOCAL_PRIMARY = "LOCAL_PRIMARY"
    DESKTOP_NODE = "DESKTOP_NODE"
    LAPTOP_NODE = "LAPTOP_NODE"
    MOBILE_NODE = "MOBILE_NODE"
    GPU_WORKER = "GPU_WORKER"
    SPECIALIST_WORKER = "SPECIALIST_WORKER"


class NodeTrustState(str, Enum):
    UNTRUSTED = "UNTRUSTED"
    ENROLLED = "ENROLLED"
    TRUSTED = "TRUSTED"
    REVOKED = "REVOKED"


class NodeCapability(str, Enum):
    BROWSER_AUTOMATION = "browser_automation"
    LOCAL_FILESYSTEM = "local_filesystem"
    DEVELOPMENT_EXECUTOR = "development_executor"
    LOCAL_VAD_WAKE = "local_vad_wake"
    GPU_INFERENCE = "gpu_inference"
    MEMORY_STORAGE = "memory_storage"
    INTERNET_RETRIEVAL = "internet_retrieval"
    VOICE_OUTPUT = "voice_output"


class NodeDescriptor(BaseModel):
    node_id: str
    node_type: NodeType
    hostname_alias: str
    public_key_hex: str
    operating_system: str
    architecture: str
    cpu_cores: int
    ram_gb: float
    gpu_model: Optional[str] = None
    available_vram_gb: Optional[float] = None
    disk_free_gb: float
    capabilities: List[NodeCapability] = Field(default_factory=list)
    services: List[str] = Field(default_factory=list)
    online_status: bool = True
    last_seen: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trust_state: NodeTrustState = NodeTrustState.UNTRUSTED
    version: str = "0.5A"


class DistributedTaskEnvelope(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_dist_{uuid.uuid4().hex[:8]}")
    parent_task_id: Optional[str] = None
    origin_node: str
    target_node: Optional[str] = None  # None = capability-routed
    requested_capability: NodeCapability
    action_type: str
    target_resource: str
    params: Dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "L1"
    approval_ref: Optional[str] = None
    deadline_epoch: Optional[float] = None
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    signature_hex: Optional[str] = None

    def compute_digest(self) -> bytes:
        payload = {
            "task_id": self.task_id,
            "origin_node": self.origin_node,
            "target_node": self.target_node,
            "requested_capability": self.requested_capability.value,
            "action_type": self.action_type,
            "target_resource": self.target_resource,
            "params": self.params,
            "risk_level": self.risk_level,
            "approval_ref": self.approval_ref,
            "trace_id": self.trace_id
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).digest()


class RemoteApprovalEvent(BaseModel):
    approval_id: str
    task_id: str
    action_type: str
    target: str
    approved: bool
    approver_node: str
    resolved_by: str = "Zak"
    nonce: str = Field(default_factory=lambda: uuid.uuid4().hex)
    expires_at: str
    signature_hex: Optional[str] = None

    def compute_digest(self) -> bytes:
        payload = {
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "action_type": self.action_type,
            "target": self.target,
            "approved": self.approved,
            "approver_node": self.approver_node,
            "resolved_by": self.resolved_by,
            "nonce": self.nonce,
            "expires_at": self.expires_at
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).digest()


class CryptographicNodeIdentity:
    """Manages local node Ed25519 keypair for cryptographic authentication."""

    def __init__(self, node_id: str, private_key: Optional[ed25519.Ed25519PrivateKey] = None):
        self.node_id = node_id
        self._private_key = private_key or ed25519.Ed25519PrivateKey.generate()
        self._public_key = self._private_key.public_key()

    @property
    def public_key_hex(self) -> str:
        return self._public_key.public_bytes_raw().hex()

    def sign(self, message: bytes) -> str:
        sig = self._private_key.sign(message)
        return sig.hex()

    @staticmethod
    def verify(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
        try:
            pub_bytes = bytes.fromhex(public_key_hex)
            sig_bytes = bytes.fromhex(signature_hex)
            pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)
            pub_key.verify(sig_bytes, message)
            return True
        except Exception:
            return False
