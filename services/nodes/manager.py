"""
HOOD Node Manager & Capability Routing Engine
Tracks trusted nodes, enforces enrollment gates, and routes tasks by capability.
Governed by Master System Specification Sections 2, 4, 9, 14 & V0.5A Node Architecture Spec.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path
import json

from services.nodes.contracts import (
    NodeType,
    NodeTrustState,
    NodeCapability,
    NodeDescriptor,
    DistributedTaskEnvelope,
    RemoteApprovalEvent,
    CryptographicNodeIdentity
)
from packages.config.paths import store_path


class NodeManager:
    """Coordinates node enrollment, verification, revocation, and capability-based task routing."""

    def __init__(self, registry_file: Optional[Path] = None):
        self.registry_file = store_path(registry_file, "artifacts/nodes/node_registry.json")
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.nodes: Dict[str, NodeDescriptor] = {}
        self.nonces_seen: set = set()
        self._load_registry()

    def _load_registry(self):
        if self.registry_file.exists():
            try:
                data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                for nid, desc_dict in data.items():
                    self.nodes[nid] = NodeDescriptor(**desc_dict)
            except Exception:
                pass

    def _save_registry(self):
        data = {nid: desc.model_dump() for nid, desc in self.nodes.items()}
        self.registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def enroll_node(self, descriptor: NodeDescriptor) -> NodeDescriptor:
        """New node initiates enrollment; default status is UNTRUSTED until authorized by Zak."""
        descriptor.trust_state = NodeTrustState.ENROLLED
        descriptor.last_seen = datetime.now(timezone.utc).isoformat()
        self.nodes[descriptor.node_id] = descriptor
        self._save_registry()
        return descriptor

    def trust_node(self, node_id: str, authorized_by: str = "Zak") -> NodeDescriptor:
        """Authorizes an enrolled node into TRUSTED status."""
        if authorized_by != "Zak":
            raise PermissionError("Only owner (Zak) can authorize nodes into TRUSTED status.")
        node = self.nodes.get(node_id)
        if not node:
            raise KeyError(f"Node '{node_id}' not found in registry.")
        node.trust_state = NodeTrustState.TRUSTED
        self._save_registry()
        return node

    def revoke_node(self, node_id: str, reason: str = "Security revocation") -> NodeDescriptor:
        """Revokes trust from a node instantly."""
        node = self.nodes.get(node_id)
        if not node:
            raise KeyError(f"Node '{node_id}' not found in registry.")
        node.trust_state = NodeTrustState.REVOKED
        node.online_status = False
        self._save_registry()
        return node

    def is_trusted(self, node_id: str) -> bool:
        node = self.nodes.get(node_id)
        return bool(node and node.trust_state == NodeTrustState.TRUSTED)

    def verify_envelope(self, envelope: DistributedTaskEnvelope) -> bool:
        """Validates envelope origin, signature, and trust status."""
        node = self.nodes.get(envelope.origin_node)
        if not node or node.trust_state != NodeTrustState.TRUSTED:
            return False
        if not envelope.signature_hex:
            return False
        digest = envelope.compute_digest()
        return CryptographicNodeIdentity.verify(node.public_key_hex, digest, envelope.signature_hex)

    def verify_remote_approval(self, approval_event: RemoteApprovalEvent) -> bool:
        """Validates remote approval event signature, trust state, expiry, and replay protection."""
        if approval_event.nonce in self.nonces_seen:
            raise PermissionError("Approval replay detected: nonce has already been used.")

        # Check expiry
        exp_dt = datetime.fromisoformat(approval_event.expires_at)
        if datetime.now(timezone.utc) > exp_dt:
            raise PermissionError("Remote approval event has expired.")

        node = self.nodes.get(approval_event.approver_node)
        if not node or node.trust_state != NodeTrustState.TRUSTED:
            raise PermissionError("Approver node is not trusted.")

        if not approval_event.signature_hex:
            raise PermissionError("Remote approval signature missing.")

        digest = approval_event.compute_digest()
        if not CryptographicNodeIdentity.verify(node.public_key_hex, digest, approval_event.signature_hex):
            raise PermissionError("Invalid cryptographic signature on approval event.")

        self.nonces_seen.add(approval_event.nonce)
        return True

    def find_node_for_capability(self, capability: NodeCapability) -> Optional[NodeDescriptor]:
        """Routes task to an active trusted node advertising the required capability."""
        for node in self.nodes.values():
            if node.trust_state == NodeTrustState.TRUSTED and node.online_status:
                if capability in node.capabilities:
                    return node
        return None

    def route_envelope(self, envelope: DistributedTaskEnvelope) -> str:
        """Assigns target node based on required capability if not explicitly specified."""
        if envelope.target_node:
            if not self.is_trusted(envelope.target_node):
                raise PermissionError(f"Target node '{envelope.target_node}' is not a trusted node.")
            return envelope.target_node

        matched_node = self.find_node_for_capability(envelope.requested_capability)
        if not matched_node:
            raise RuntimeError(f"No trusted online node available with capability '{envelope.requested_capability.value}'.")
        envelope.target_node = matched_node.node_id
        return matched_node.node_id

    def list_nodes(self) -> List[Dict[str, Any]]:
        return [desc.model_dump() for desc in self.nodes.values()]
