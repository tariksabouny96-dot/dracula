"""
HOOD Remote Tool Proxy & Multi-Node Tool Gateway Integration
Governed by Master System Specification Sections 9, 12, 15 & V0.5A Multi-Node Foundation.

Enables the local Tool Gateway to delegate execution to trusted remote nodes
via NodeTransportClient while enforcing:
1. Origin node caller governance and capability checks.
2. Cryptographic signature and envelope verification.
3. Destination node boundary re-validation (the executing node is the final authority).
4. Reversible execution and failure containment.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone

from services.tool_gateway.gateway import BaseTool, ToolGateway, PermissionDeniedError
from services.transport.node_transport import NodeTransportClient
from services.nodes.contracts import DistributedTaskEnvelope, CryptographicNodeIdentity, NodeCapability


class RemoteToolProxy(BaseTool):
    """
    Proxies tool execution across nodes over authenticated transport.
    The local node issues a signed task envelope; the remote node's
    transport server receives, verifies, and executes via its own local ToolGateway.
    """

    def __init__(
        self,
        name: str,
        target_node_id: str,
        target_port: int,
        sender_identity: CryptographicNodeIdentity,
        requested_capability: NodeCapability = NodeCapability.DEVELOPMENT_EXECUTOR,
        description: str = "Proxies tool execution to a trusted remote node."
    ):
        super().__init__(name=name, required_capability=requested_capability.value, description=description)
        self.target_node_id = target_node_id
        self.target_port = target_port
        self.sender_identity = sender_identity
        self.requested_capability = requested_capability

    def execute(self, params: Dict[str, Any]) -> Any:
        """
        Package tool call into a signed DistributedTaskEnvelope and dispatch
        to the target node's transport port.
        """
        task_id = params.get("task_id", f"task_dist_{int(datetime.now(timezone.utc).timestamp())}")
        target_tool = params.get("remote_tool_name", self.name)
        tool_params = params.get("remote_params", params)

        envelope = DistributedTaskEnvelope(
            task_id=task_id,
            origin_node=self.sender_identity.node_id,
            target_node=self.target_node_id,
            requested_capability=self.requested_capability,
            action_type="tool_execution",
            target_resource=str(tool_params.get("path") or target_tool),
            params={
                "tool_name": target_tool,
                "params": tool_params,
                "caller_agent": params.get("caller_agent", "Hood")
            },
            risk_level=params.get("risk_level", "L1"),
            approval_ref=params.get("approval_id")
        )

        response = NodeTransportClient.send_envelope(
            target_port=self.target_port,
            envelope=envelope,
            sender_identity=self.sender_identity
        )

        if not response.get("success", False) and "result" not in response:
            error_msg = response.get("error", "Unknown remote execution failure")
            raise PermissionDeniedError(f"Remote execution on node '{self.target_node_id}' failed: {error_msg}")

        return response.get("result", response)
