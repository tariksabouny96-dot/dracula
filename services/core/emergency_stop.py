"""
HOOD Emergency Stop Controller
Preserves state, revokes capabilities, and halts execution instantly upon command.
Governed by Master System Specification Section 14.2 & Acceptance Scenario A04.
"""

from typing import Dict, Any, List
from datetime import datetime, timezone
from packages.contracts import SystemState, TaskStatus, TaskNode
from services.tool_gateway.gateway import ToolGateway
from services.audit.service import AuditService


class EmergencyStopTriggeredError(Exception):
    """Raised when an operation is attempted while Emergency Stop is active."""
    pass


class EmergencyStopController:
    def __init__(
        self,
        tool_gateway: ToolGateway,
        audit_service: AuditService,
        browser_service=None,
        dev_executor=None,
        event_bus=None,
        node_manager=None,
        evolution_engine=None,
        desktop_service=None
    ):
        self.tool_gateway = tool_gateway
        self.audit_service = audit_service
        self.browser_service = browser_service
        self.dev_executor = dev_executor
        self.event_bus = event_bus
        self.node_manager = node_manager
        self.evolution_engine = evolution_engine
        self.desktop_service = desktop_service
        self.system_state: SystemState = SystemState.IDLE
        self.stopped_at: datetime = None
        self.stop_reason: str = ""
        self.extra_executors: Dict[str, Any] = {}
        # A controller created over an already-latched gateway (e.g. after restart) starts stopped.
        if tool_gateway is not None and tool_gateway.stop_latch.engaged:
            self.system_state = SystemState.EMERGENCY_STOP
            self.stop_reason = tool_gateway.stop_latch.reason

    @property
    def is_active(self) -> bool:
        return self.system_state == SystemState.EMERGENCY_STOP

    def attach(self, name: str, halt_callable) -> None:
        """Register an additional executor (e.g. the agent engine) to be halted on stop."""
        self.extra_executors[name] = halt_callable

    def trigger_stop(self, reason: str = "Owner emergency stop command ('Hood, stop everything')") -> Dict[str, Any]:
        """Halt all execution and report, per subsystem, what was actually reached.

        The result never claims a subsystem was stopped unless its halt call
        returned without error; detached subsystems are reported NOT_ATTACHED.
        """
        self.system_state = SystemState.EMERGENCY_STOP
        self.stopped_at = datetime.now(timezone.utc)
        self.stop_reason = reason
        subsystems: Dict[str, Dict[str, Any]] = {}

        def run(name, target, fn):
            if target is None:
                subsystems[name] = {"status": "NOT_ATTACHED"}
                return
            try:
                detail = fn()
                subsystems[name] = {"status": "HALTED", "detail": detail}
            except Exception as exc:  # report, never hide, a failed halt
                subsystems[name] = {"status": "ERROR", "error": str(exc)[:300]}

        # 1. Engage the latch and revoke all capability grants (blocks new tool runs).
        run("tool_gateway", self.tool_gateway,
            lambda: {"grants_revoked": self.tool_gateway.halt(reason)})
        run("browser", self.browser_service, lambda: self.browser_service.close())
        run("dev_executor", self.dev_executor, lambda: self.dev_executor.stop_all_services())

        def publish():
            from services.events.event_bus import DistributedEvent, DistributedEventType
            ident = getattr(self.node_manager, "local_identity", None) if self.node_manager else None
            self.event_bus.publish(DistributedEvent(
                event_type=DistributedEventType.EMERGENCY_STOP,
                source_node=ident.node_id if ident else "local",
                payload={"reason": reason, "timestamp": self.stopped_at.isoformat()}))
        run("event_bus", self.event_bus, publish)
        # The evolution engine has no cancellable trial API; never claim it was halted.
        if self.evolution_engine is not None:
            subsystems["evolution"] = {"status": "UNSUPPORTED", "detail": "no cancellation API"}
        else:
            subsystems["evolution"] = {"status": "NOT_ATTACHED"}
        run("desktop", self.desktop_service, lambda: self.desktop_service.halt())
        for name, fn in list(self.extra_executors.items()):
            run(name, fn, fn)

        failed = [n for n, r in subsystems.items() if r["status"] in ("ERROR", "UNSUPPORTED")]
        latch_engaged = bool(self.tool_gateway and self.tool_gateway.stop_latch.engaged)

        if self.audit_service:
            from packages.contracts import AuditEvent
            self.audit_service.record_event(AuditEvent(
                actor="SYSTEM",
                action="EMERGENCY_STOP",
                target="SYSTEM",
                policy_decision="HALT",
                result=f"Stop requested: {reason}; subsystems={ {k: v['status'] for k, v in subsystems.items()} }",
                verification="PARTIAL" if failed or not latch_engaged else "LATCH_ENGAGED"
            ))

        return {
            "status": "EMERGENCY_STOP_ACTIVE",
            "timestamp": self.stopped_at.isoformat(),
            "reason": reason,
            "capabilities_revoked": subsystems["tool_gateway"]["status"] == "HALTED",
            "latch_engaged": latch_engaged,
            "subsystems": subsystems,
            "incomplete": failed,
        }

    def reset_stop(self, authorized_by: str = "", is_root_owner: bool = False) -> None:
        """Release the stop only on an authenticated Root Owner decision."""
        if not is_root_owner:
            raise PermissionError("Only the authenticated Root Owner can reset the emergency stop.")
        if self.tool_gateway:
            self.tool_gateway.stop_latch.release()
        if self.audit_service:
            from packages.contracts import AuditEvent
            self.audit_service.record_event(AuditEvent(
                actor=authorized_by or "ROOT_OWNER", action="EMERGENCY_STOP_RESET", target="SYSTEM",
                policy_decision="ALLOW", result="Stop latch released", verification="LATCH_RELEASED"))
        self.system_state = SystemState.IDLE
        self.stopped_at = None
        self.stop_reason = ""
