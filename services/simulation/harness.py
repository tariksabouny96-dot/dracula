"""Deterministic, side-effect-free integration simulation for HOOD boundaries.

Simulation output is always marked SIMULATED and must never be promoted to
real-device or provider acceptance evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any
import json


@dataclass(frozen=True)
class SimulationEvent:
    component: str
    operation: str
    status: str
    payload_hash: str
    simulated: bool = True


@dataclass
class ControlledSimulation:
    """No network, filesystem, microphone, keyboard or window access."""

    events: list[SimulationEvent] = field(default_factory=list)
    stopped: bool = False
    approved: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def _digest(payload: dict[str, Any]) -> str:
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def authorize(self, action_id: str, *, component: str, operation: str, parameters: dict[str, Any]) -> None:
        if self.stopped:
            raise PermissionError("Emergency stop active")
        self.approved[action_id] = self._digest({"component": component, "operation": operation, "parameters": parameters})

    def execute(self, component: str, operation: str, parameters: dict[str, Any], *,
                action_id: str | None = None, consequential: bool = False) -> SimulationEvent:
        if self.stopped:
            raise PermissionError("Emergency stop active")
        digest = self._digest({"component": component, "operation": operation, "parameters": parameters})
        if consequential:
            if not action_id or self.approved.get(action_id) != digest:
                raise PermissionError("Missing or mismatched action-specific approval")
            del self.approved[action_id]
        event = SimulationEvent(component, operation, "SIMULATED", digest)
        self.events.append(event)
        return event

    def emergency_stop(self) -> None:
        self.stopped = True
        self.approved.clear()

    def report(self) -> dict[str, Any]:
        events = [asdict(e) for e in self.events]
        return {
            "mode": "CONTROLLED_SIMULATION",
            "real_operations_executed": 0,
            "production_acceptance": False,
            "events": events,
            "event_log_sha256": self._digest({"events": events}),
        }


def demonstration() -> dict[str, Any]:
    """Exercise multiple integration contracts with strictly inert adapters."""
    sim = ControlledSimulation()
    sim.execute("voice", "transcribe_fixture", {"fixture": "hello hood"})
    sim.execute("browser", "navigate_fixture", {"url": "http://example.invalid/mock"})
    sim.execute("provider", "structured_response_fixture", {"model": "fixture"})
    params = {"target": "sandbox-only", "text": "test"}
    sim.authorize("desktop-001", component="desktop", operation="type_fixture", parameters=params)
    sim.execute("desktop", "type_fixture", params, consequential=True, action_id="desktop-001")
    sim.emergency_stop()
    report = sim.report()
    report["emergency_stop_enforced"] = sim.stopped
    return report
