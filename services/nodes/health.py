"""
HOOD Node Health Monitor & Telemetry Collector
Governed by Master System Specification Sections 6, 12, 14 & V0.5A Multi-Node Foundation.

Tracks node heartbeats, latency, resource metrics (CPU, RAM, Disk),
and automatically transitions inactive nodes to offline status while
publishing lifecycle events over the DistributedEventBus.
"""

import os
import psutil
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from services.nodes.contracts import NodeDescriptor, NodeTrustState
from services.nodes.manager import NodeManager
from services.events.event_bus import DistributedEventBus, DistributedEvent, DistributedEventType


class NodeHealthMonitor:
    def __init__(
        self,
        node_manager: NodeManager,
        event_bus: Optional[DistributedEventBus] = None,
        heartbeat_timeout_seconds: float = 30.0
    ):
        self.node_manager = node_manager
        self.event_bus = event_bus
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds

    def record_heartbeat(self, node_id: str, metrics: Optional[Dict[str, Any]] = None) -> bool:
        """Records a heartbeat from a node, updating its timestamp and health metrics."""
        node = self.node_manager.nodes.get(node_id)
        if not node:
            return False

        now = datetime.now(timezone.utc)
        was_offline = not node.online_status
        
        node.last_seen = now.isoformat()
        node.online_status = True

        if metrics:
            if "ram_gb" in metrics:
                node.ram_gb = metrics["ram_gb"]
            if "disk_free_gb" in metrics:
                node.disk_free_gb = metrics["disk_free_gb"]

        if was_offline and self.event_bus:
            self.event_bus.publish(DistributedEvent(
                event_type=DistributedEventType.NODE_ONLINE,
                source_node=node_id,
                payload={"node_id": node_id, "hostname_alias": node.hostname_alias}
            ))

        return True

    def collect_local_metrics(self) -> Dict[str, Any]:
        """Collects host CPU, memory, and disk utilization metrics safely."""
        metrics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_available_mb": round(psutil.virtual_memory().available / (1024 * 1024), 2),
            "disk_free_gb": round(psutil.disk_usage('.').free / (1024 * 1024 * 1024), 2)
        }
        return metrics

    def sweep_stale_nodes(self) -> List[str]:
        """
        Scans all known nodes and transitions nodes that missed their heartbeat window
        to offline status.
        """
        now = datetime.now(timezone.utc)
        marked_offline = []

        for node_id, node in self.node_manager.nodes.items():
            if node.trust_state == NodeTrustState.REVOKED:
                continue

            if node.online_status:
                last_dt = datetime.fromisoformat(node.last_seen)
                if (now - last_dt).total_seconds() > self.heartbeat_timeout_seconds:
                    node.online_status = False
                    marked_offline.append(node_id)
                    if self.event_bus:
                        self.event_bus.publish(DistributedEvent(
                            event_type=DistributedEventType.NODE_OFFLINE,
                            source_node=node_id,
                            payload={
                                "node_id": node_id,
                                "hostname_alias": node.hostname_alias,
                                "seconds_since_heartbeat": (now - last_dt).total_seconds()
                            }
                        ))

        return marked_offline
