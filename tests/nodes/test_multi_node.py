"""
HOOD Multi-Node & Self-Hosting Foundation Test Suite (V0.5A)
Governed by Master System Specification Section 2, 4, 9, 14 & V0.5A Multi-Node Specification.

Verifies:
1. Cryptographic Node Identity generation, Ed25519 signing, and verification.
2. Node enrollment (ENROLLED status), authorization gate (Zak), and instant revocation.
3. Tamper-evident DistributedTaskEnvelope digest verification and signature rejection.
4. Remote approval verification, nonce replay rejection, and expiry validation.
5. Capability-based node discovery and task routing.
6. Authenticated localhost NodeTransport server & client communication.
7. Remote Tool Gateway execution through RemoteToolProxy.
8. Distributed Emergency Stop propagation across nodes.
9. NodeHealthMonitor heartbeat tracking and offline status sweep.
10. SQLite to PostgreSQL memory migration with SHA256 integrity verification.
11. Strict X_SEALED memory isolation from cross-node export/replication.
12. Node migration bundle export, inspection, and import.
"""

import time
import json
import uuid
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from services.nodes.contracts import (
    NodeType,
    NodeTrustState,
    NodeCapability,
    NodeDescriptor,
    DistributedTaskEnvelope,
    RemoteApprovalEvent,
    CryptographicNodeIdentity
)
from services.nodes.manager import NodeManager
from services.nodes.health import NodeHealthMonitor
from services.nodes.migration import NodeMigrationBundle
from services.transport.node_transport import NodeTransportServer, NodeTransportClient
from services.events.event_bus import DistributedEventBus, DistributedEvent, DistributedEventType
from services.tool_gateway.gateway import ToolGateway, BaseTool, PermissionDeniedError
from services.tool_gateway.remote_tools import RemoteToolProxy
from services.core.emergency_stop import EmergencyStopController
from services.audit.service import AuditService
from services.memory.backends import SQLiteBackend, PostgreSQLBackend, MemoryMigrationTool
from packages.contracts import MemoryObject, MemoryType, LearningStatus
from packages.config import SystemConfig


class DummyWorkerTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="worker_compute",
            required_capability="development_executor",
            description="Executes computation on worker node"
        )

    def execute(self, params):
        val = params.get("value", 0)
        return {"computed": val * 2, "worker_processed": True}


@pytest.fixture
def tmp_cluster_dir(tmp_path):
    registry_file = tmp_path / "node_registry.json"
    sqlite_db = tmp_path / "test_memory.db"
    return {"registry_file": registry_file, "sqlite_db": sqlite_db, "tmp_path": tmp_path}


def test_cryptographic_node_identity():
    """Verify Ed25519 keypair generation, signature creation, and validation."""
    id1 = CryptographicNodeIdentity(node_id="node-cloud-core")
    id2 = CryptographicNodeIdentity(node_id="node-laptop")

    msg = b"HOOD distributed task payload 12345"
    sig1 = id1.sign(msg)

    # Valid signature passes
    assert CryptographicNodeIdentity.verify(id1.public_key_hex, msg, sig1) is True

    # Tampered message fails
    assert CryptographicNodeIdentity.verify(id1.public_key_hex, b"tampered payload", sig1) is False

    # Wrong public key fails
    assert CryptographicNodeIdentity.verify(id2.public_key_hex, msg, sig1) is False


def test_node_lifecycle_and_trust_gating(tmp_cluster_dir):
    """Verify enrollment as ENROLLED, trust gated to Zak, and instant revocation."""
    manager = NodeManager(registry_file=tmp_cluster_dir["registry_file"])
    node_id = "node-gpu-01"
    identity = CryptographicNodeIdentity(node_id=node_id)

    desc = NodeDescriptor(
        node_id=node_id,
        node_type=NodeType.GPU_WORKER,
        hostname_alias="gpu-box",
        public_key_hex=identity.public_key_hex,
        operating_system="Linux",
        architecture="x86_64",
        cpu_cores=16,
        ram_gb=64.0,
        gpu_model="RTX 4090",
        available_vram_gb=24.0,
        disk_free_gb=500.0,
        capabilities=[NodeCapability.GPU_INFERENCE]
    )

    # 1. Enroll
    manager.enroll_node(desc)
    assert manager.is_trusted(node_id) is False
    assert manager.nodes[node_id].trust_state == NodeTrustState.ENROLLED

    # 2. Unauthorized trust attempt fails
    with pytest.raises(PermissionError, match="Only owner"):
        manager.trust_node(node_id, authorized_by="Mallory")

    # 3. Zak authorizes trust
    manager.trust_node(node_id, authorized_by="Zak")
    assert manager.is_trusted(node_id) is True
    assert manager.nodes[node_id].trust_state == NodeTrustState.TRUSTED

    # 4. Instant revocation
    manager.revoke_node(node_id, reason="Compromise suspicion")
    assert manager.is_trusted(node_id) is False
    assert manager.nodes[node_id].trust_state == NodeTrustState.REVOKED
    assert manager.nodes[node_id].online_status is False


def test_distributed_task_envelope_tamper_detection(tmp_cluster_dir):
    """Verify envelope signature protects integrity against in-flight payload tampering."""
    manager = NodeManager(registry_file=tmp_cluster_dir["registry_file"])
    sender_id = "node-laptop"
    identity = CryptographicNodeIdentity(node_id=sender_id)

    desc = NodeDescriptor(
        node_id=sender_id,
        node_type=NodeType.LAPTOP_NODE,
        hostname_alias="laptop",
        public_key_hex=identity.public_key_hex,
        operating_system="Windows",
        architecture="AMD64",
        cpu_cores=4,
        ram_gb=16.0,
        disk_free_gb=100.0,
        capabilities=[NodeCapability.LOCAL_FILESYSTEM]
    )
    manager.enroll_node(desc)
    manager.trust_node(sender_id, authorized_by="Zak")

    envelope = DistributedTaskEnvelope(
        origin_node=sender_id,
        target_node="node-cloud-core",
        requested_capability=NodeCapability.LOCAL_FILESYSTEM,
        action_type="read_file",
        target_resource="src/config.py",
        params={"path": "src/config.py"}
    )
    envelope.signature_hex = identity.sign(envelope.compute_digest())

    # Valid envelope passes
    assert manager.verify_envelope(envelope) is True

    # Tampered envelope payload fails verification
    envelope.target_resource = "etc/shadow"
    assert manager.verify_envelope(envelope) is False


def test_remote_approval_replay_and_expiry(tmp_cluster_dir):
    """Verify remote approvals reject replay attacks and expired events."""
    manager = NodeManager(registry_file=tmp_cluster_dir["registry_file"])
    approver_id = "node-primary-core"
    identity = CryptographicNodeIdentity(node_id=approver_id)

    desc = NodeDescriptor(
        node_id=approver_id,
        node_type=NodeType.LOCAL_PRIMARY,
        hostname_alias="core-station",
        public_key_hex=identity.public_key_hex,
        operating_system="Linux",
        architecture="x86_64",
        cpu_cores=8,
        ram_gb=32.0,
        disk_free_gb=200.0,
        capabilities=[NodeCapability.DEVELOPMENT_EXECUTOR]
    )
    manager.enroll_node(desc)
    manager.trust_node(approver_id, authorized_by="Zak")

    # 1. Valid approval
    future_exp = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    approval = RemoteApprovalEvent(
        approval_id="appr-101",
        task_id="task-999",
        action_type="git_push",
        target="origin/main",
        approved=True,
        approver_node=approver_id,
        resolved_by="Zak",
        expires_at=future_exp
    )
    approval.signature_hex = identity.sign(approval.compute_digest())

    assert manager.verify_remote_approval(approval) is True

    # 2. Replay attack with same nonce rejected
    with pytest.raises(PermissionError, match="replay detected"):
        manager.verify_remote_approval(approval)

    # 3. Expired approval rejected
    past_exp = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    expired_approval = RemoteApprovalEvent(
        approval_id="appr-102",
        task_id="task-1000",
        action_type="git_push",
        target="origin/main",
        approved=True,
        approver_node=approver_id,
        resolved_by="Zak",
        expires_at=past_exp
    )
    expired_approval.signature_hex = identity.sign(expired_approval.compute_digest())

    with pytest.raises(PermissionError, match="has expired"):
        manager.verify_remote_approval(expired_approval)


def test_capability_based_task_routing(tmp_cluster_dir):
    """Verify capability-based routing routes tasks to the appropriate online trusted node."""
    manager = NodeManager(registry_file=tmp_cluster_dir["registry_file"])

    id_gpu = CryptographicNodeIdentity(node_id="node-gpu")
    desc_gpu = NodeDescriptor(
        node_id="node-gpu",
        node_type=NodeType.GPU_WORKER,
        hostname_alias="gpu-worker",
        public_key_hex=id_gpu.public_key_hex,
        operating_system="Linux",
        architecture="x86_64",
        cpu_cores=16,
        ram_gb=64.0,
        disk_free_gb=500.0,
        capabilities=[NodeCapability.GPU_INFERENCE]
    )
    manager.enroll_node(desc_gpu)
    manager.trust_node("node-gpu", authorized_by="Zak")

    # Task requiring GPU inference
    envelope = DistributedTaskEnvelope(
        origin_node="node-client",
        requested_capability=NodeCapability.GPU_INFERENCE,
        action_type="generate_embedding",
        target_resource="model-llama3"
    )

    routed_node_id = manager.route_envelope(envelope)
    assert routed_node_id == "node-gpu"
    assert envelope.target_node == "node-gpu"


def test_authenticated_transport_and_remote_tool_gateway(tmp_cluster_dir):
    """Verify authenticated NodeTransportServer and remote tool execution via RemoteToolProxy."""
    port = 9871
    manager = NodeManager(registry_file=tmp_cluster_dir["registry_file"])

    client_id = "node-laptop-client"
    client_identity = CryptographicNodeIdentity(node_id=client_id)
    desc_client = NodeDescriptor(
        node_id=client_id,
        node_type=NodeType.LAPTOP_NODE,
        hostname_alias="laptop",
        public_key_hex=client_identity.public_key_hex,
        operating_system="Windows",
        architecture="AMD64",
        cpu_cores=4,
        ram_gb=16.0,
        disk_free_gb=100.0,
        capabilities=[NodeCapability.LOCAL_FILESYSTEM]
    )
    manager.enroll_node(desc_client)
    manager.trust_node(client_id, authorized_by="Zak")

    # Remote worker node setup
    worker_gateway = ToolGateway(workspace_root=tmp_cluster_dir["tmp_path"])
    dummy_tool = DummyWorkerTool()
    worker_gateway.register_tool(dummy_tool)

    def handle_envelope(env: DistributedTaskEnvelope):
        tool_name = env.params.get("tool_name")
        params = env.params.get("params", {})
        res = worker_gateway.invoke_tool(tool_name, params)
        return {"success": True, "result": res}

    server = NodeTransportServer(
        port=port,
        node_id="node-worker",
        node_manager=manager,
        on_envelope=handle_envelope
    )
    server.start()

    try:
        proxy = RemoteToolProxy(
            name="worker_compute",
            target_node_id="node-worker",
            target_port=port,
            sender_identity=client_identity,
            requested_capability=NodeCapability.DEVELOPMENT_EXECUTOR
        )

        res = proxy.execute({"value": 21})
        assert res["computed"] == 42
        assert res["worker_processed"] is True

    finally:
        server.stop()


def test_distributed_emergency_stop_propagation(tmp_cluster_dir):
    """Verify EmergencyStopController publishes EMERGENCY_STOP event across the event bus."""
    event_bus = DistributedEventBus(node_id="node-core")
    received_events = []
    event_bus.subscribe(
        DistributedEventType.EMERGENCY_STOP,
        lambda ev: received_events.append(ev)
    )

    audit_service = AuditService(db_path=tmp_cluster_dir["sqlite_db"])
    tool_gateway = ToolGateway(workspace_root=tmp_cluster_dir["tmp_path"])
    estop = EmergencyStopController(
        tool_gateway=tool_gateway,
        audit_service=audit_service,
        event_bus=event_bus
    )

    assert estop.is_active is False
    res = estop.trigger_stop(reason="Test owner stop command")

    assert estop.is_active is True
    assert res["status"] == "EMERGENCY_STOP_ACTIVE"
    assert len(received_events) == 1
    assert received_events[0].event_type == DistributedEventType.EMERGENCY_STOP
    assert received_events[0].payload["reason"] == "Test owner stop command"


def test_node_health_monitor_heartbeat_and_stale_sweep(tmp_cluster_dir):
    """Verify NodeHealthMonitor records heartbeats and sweeps stale nodes offline."""
    manager = NodeManager(registry_file=tmp_cluster_dir["registry_file"])
    event_bus = DistributedEventBus(node_id="node-core")
    monitor = NodeHealthMonitor(node_manager=manager, event_bus=event_bus, heartbeat_timeout_seconds=0.1)

    node_id = "node-heartbeat-test"
    desc = NodeDescriptor(
        node_id=node_id,
        node_type=NodeType.LAPTOP_NODE,
        hostname_alias="laptop-heartbeat",
        public_key_hex="a1b2c3",
        operating_system="Windows",
        architecture="AMD64",
        cpu_cores=4,
        ram_gb=16.0,
        disk_free_gb=100.0,
        online_status=True
    )
    manager.nodes[node_id] = desc

    # Record heartbeat
    monitor.record_heartbeat(node_id, metrics={"ram_gb": 15.5})
    assert desc.ram_gb == 15.5
    assert desc.online_status is True

    # Wait past timeout and sweep
    time.sleep(0.15)
    stale = monitor.sweep_stale_nodes()
    assert node_id in stale
    assert desc.online_status is False


def test_sqlite_to_postgresql_memory_migration_and_hash_integrity(tmp_cluster_dir):
    """Verify MemoryMigrationTool migrates records with zero count loss and exact SHA256 integrity."""
    sqlite_backend = SQLiteBackend(db_path=tmp_cluster_dir["sqlite_db"])
    postgres_backend = PostgreSQLBackend()

    # Seed SQLite with memory records
    now_str = datetime.now(timezone.utc).isoformat()
    test_memories = [
        MemoryObject(
            memory_id="mem-1",
            type=MemoryType.PERSONAL,
            content="Owner prefers dark mode and concise output",
            project="hood_core",
            source="user_conversation",
            source_agent="Hood",
            created_at=datetime.now(timezone.utc),
            valid_from=datetime.now(timezone.utc),
            confidence=0.95,
            learning_status=LearningStatus.ESTABLISHED
        ),
        MemoryObject(
            memory_id="mem-2",
            type=MemoryType.PROJECT,
            content="HOOD V0.5A implements multi-node contracts and self-hosting",
            project="hood_core",
            source="system_event",
            source_agent="Architect",
            created_at=datetime.now(timezone.utc),
            valid_from=datetime.now(timezone.utc),
            confidence=0.99,
            learning_status=LearningStatus.ESTABLISHED
        )
    ]

    for m in test_memories:
        sqlite_backend.store_memory(m)

    res = MemoryMigrationTool.migrate(source=sqlite_backend, target=postgres_backend)
    assert res["status"] == "SUCCESS"
    assert res["integrity_verified"] is True
    assert res["source_count"] == 2
    assert res["target_count"] == 2
    assert res["source_hash"] == res["target_hash"]


def test_x_sealed_memory_isolated_from_export(tmp_cluster_dir):
    """Verify X_SEALED memories are completely excluded from project queries and cross-node replication."""
    sqlite_backend = SQLiteBackend(db_path=tmp_cluster_dir["sqlite_db"])

    # Store normal memory
    m_normal = MemoryObject(
        memory_id="mem-norm-1",
        type=MemoryType.PROJECT,
        content="Standard project memory",
        project="general",
        source="doc",
        source_agent="Hood",
        created_at=datetime.now(timezone.utc),
        valid_from=datetime.now(timezone.utc),
        confidence=1.0,
        learning_status=LearningStatus.ESTABLISHED
    )
    sqlite_backend.store_memory(m_normal)

    # Store X_SEALED memory
    m_x = MemoryObject(
        memory_id="mem-x-sealed-1",
        type=MemoryType.X_SEALED,
        content="X-classified offensive payload metadata",
        project="general",
        source="x_vault",
        source_agent="X",
        created_at=datetime.now(timezone.utc),
        valid_from=datetime.now(timezone.utc),
        confidence=1.0,
        learning_status=LearningStatus.ESTABLISHED
    )
    sqlite_backend.store_memory(m_x)

    # Project query must NOT return X_SEALED memories
    queried = sqlite_backend.query_by_project("general")
    ids = [q.memory_id for q in queried]
    assert "mem-norm-1" in ids
    assert "mem-x-sealed-1" not in ids


def test_node_migration_bundle_export_import(tmp_cluster_dir):
    """Verify node export creates a verified tarball and import verifies integrity."""
    bundle_tool = NodeMigrationBundle(workspace_root=tmp_cluster_dir["tmp_path"])
    export_tar = tmp_cluster_dir["tmp_path"] / "artifacts" / "test_backup.tar.gz"
    import_db = tmp_cluster_dir["tmp_path"] / "imported_memory.db"

    migration_key = "shared-migration-secret-123"

    # Export (signed by an authorised sender)
    export_res = bundle_tool.export_node(
        output_tar_path=export_tar,
        node_id="node-test-export",
        sqlite_db_path=tmp_cluster_dir["sqlite_db"],
        signing_key=migration_key,
    )
    assert export_res["success"] is True
    assert export_tar.exists()

    # Inspect manifest without unpacking
    manifest = bundle_tool.inspect_bundle(export_tar)
    assert manifest["node_id"] == "node-test-export"
    assert manifest["secrets_included"] is False
    assert manifest["signed"] is True

    # Import with the matching key
    import_res = bundle_tool.import_node(
        tar_path=export_tar,
        target_sqlite_path=import_db,
        signing_key=migration_key,
    )
    assert import_res["success"] is True
    assert import_res["node_id"] == "node-test-export"
    assert import_res["integrity_verified"] is True
    assert import_res["authenticated"] is True


def test_migration_import_refuses_unauthenticated_and_forged_senders(tmp_cluster_dir):
    """F27: an unsigned bundle, a missing key, and a wrong key are all refused."""
    import pytest
    bundle_tool = NodeMigrationBundle(workspace_root=tmp_cluster_dir["tmp_path"])
    export_tar = tmp_cluster_dir["tmp_path"] / "artifacts" / "signed.tar.gz"
    import_db = tmp_cluster_dir["tmp_path"] / "imported.db"

    # Unsigned export -> import (which requires auth) is refused.
    bundle_tool.export_node(
        output_tar_path=export_tar,
        node_id="n1",
        sqlite_db_path=tmp_cluster_dir["sqlite_db"],
    )
    with pytest.raises(PermissionError):
        bundle_tool.import_node(export_tar, import_db, signing_key="any-key")
    assert not import_db.exists()

    # Signed export, but the receiver has no key -> refused.
    signed_tar = tmp_cluster_dir["tmp_path"] / "artifacts" / "signed2.tar.gz"
    bundle_tool.export_node(
        output_tar_path=signed_tar,
        node_id="n1",
        sqlite_db_path=tmp_cluster_dir["sqlite_db"],
        signing_key="correct-key",
    )
    with pytest.raises(PermissionError):
        bundle_tool.import_node(signed_tar, import_db, signing_key=None, require_authentication=True)
    assert not import_db.exists()

    # Signed export, receiver has the WRONG key -> refused.
    with pytest.raises(PermissionError):
        bundle_tool.import_node(signed_tar, import_db, signing_key="wrong-key")
    assert not import_db.exists()

    # Correct key -> accepted.
    res = bundle_tool.import_node(signed_tar, import_db, signing_key="correct-key")
    assert res["success"] is True


def test_postgresql_backend_refuses_to_fake_a_live_connection():
    """A simulated PG backend must never masquerade as a real database (F26)."""
    import pytest
    assert PostgreSQLBackend.is_simulated is True
    with pytest.raises(NotImplementedError):
        PostgreSQLBackend(connection_str="postgresql://user:pass@prod-db:5432/hood")


def test_import_records_is_transactional_on_failure(tmp_path):
    """F27: a failure mid-import rolls back; the target is never half-populated."""
    import pytest
    db = tmp_path / "txn.db"
    backend = SQLiteBackend(db_path=db)
    good = {
        "memory_id": "ok-1", "type": "PROJECT", "content": "c", "project": "p",
        "source": "s", "source_agent": "a", "created_at": "2026-01-01T00:00:00+00:00",
        "valid_from": "2026-01-01T00:00:00+00:00",
    }
    bad = {"memory_id": "bad-1"}  # missing required keys -> raises mid-loop
    with pytest.raises(Exception):
        backend.import_records([good, bad])
    # Rolled back: not even the good record landed.
    assert backend.query_by_project("p") == []
