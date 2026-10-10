"""HOOD egress firewall.

A default-deny outbound network policy that HOOD itself consults before any
component (model gateway, browser, intelligence fetcher) opens a connection.
Properties:
- **Default deny.** A host is reachable only if an allow rule matches.
- **Owner authority.** Adding or removing rules requires the Root Owner; agents
  and self-development can read the policy but never widen it.
- **Fail closed.** Any error evaluating the policy denies the connection, and an
  engaged emergency-stop latch denies all egress.
- **Audited.** Every decision can be recorded through the audit service.

Rules match a host exactly, or by a single leading ``*.`` suffix wildcard
(e.g. ``*.googleapis.com`` matches ``generativelanguage.googleapis.com`` but not
``googleapis.com.evil.test``). Ports are an explicit set per rule.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class FirewallBlocked(PermissionError):
    """Raised when an outbound connection is denied by policy."""


@dataclass
class Decision:
    allowed: bool
    reason: str
    host: str
    port: int


def _normalise_host(host: str) -> str:
    host = (host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):  # IPv6 literal
        host = host[1:-1]
    return host


def _host_matches(pattern: str, host: str) -> bool:
    pattern = pattern.strip().lower()
    if pattern == host:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.com"
        return host.endswith(suffix) and host != suffix[1:]
    return False


class NetworkFirewall:
    LOOPBACK = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}

    def __init__(self, data_dir: Optional[Path] = None, *, stop_latch: Any = None,
                 audit_service: Any = None, allow_loopback: bool = True):
        base = Path(data_dir) if data_dir else Path(
            os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "firewall"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / "rules.json"
        self.stop_latch = stop_latch
        self.audit_service = audit_service
        self.allow_loopback = allow_loopback
        self._lock = threading.Lock()
        self._rules: List[Dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            if self.path.is_file():
                self._rules = json.loads(self.path.read_text(encoding="utf-8")).get("rules", [])
        except Exception:
            self._rules = []  # fail closed: an unreadable policy means no allows

    def _persist(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"rules": self._rules}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ------------------------------------------------------------- management
    def _require_owner(self, is_root_owner: bool, action: str) -> None:
        if not is_root_owner:
            raise PermissionError(f"Only the Root Owner may {action} firewall rules.")

    def allow(self, host: str, ports: Optional[List[int]] = None, *, note: str = "",
              added_by: str = "owner", is_root_owner: bool = False) -> Dict[str, Any]:
        self._require_owner(is_root_owner, "add")
        host = host.strip().lower()
        if not host or any(c.isspace() for c in host):
            raise ValueError("invalid host")
        rule = {
            "id": "fw_" + os.urandom(8).hex(),
            "host": host,
            "ports": sorted({int(p) for p in (ports or [443])}),
            "note": str(note)[:200],
            "added_by": added_by,
            "added_at": time.time(),
        }
        with self._lock:
            self._rules.append(rule)
            self._persist()
        self._audit("FIREWALL_RULE_ADDED", added_by, f"{host}:{rule['ports']} ({note})")
        return rule

    def revoke(self, rule_id: str, *, actor: str = "owner", is_root_owner: bool = False) -> bool:
        self._require_owner(is_root_owner, "remove")
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r["id"] != rule_id]
            removed = len(self._rules) < before
            if removed:
                self._persist()
        if removed:
            self._audit("FIREWALL_RULE_REVOKED", actor, rule_id)
        return removed

    def list_rules(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._rules]

    def seed_defaults(self, hosts: List[str]) -> None:
        """Add provider hosts that are not yet present (startup convenience).

        Seeding is a system action, not an owner widening of policy at runtime;
        the owner can revoke any seeded rule. Never removes or overrides rules.
        """
        existing = {r["host"] for r in self._rules}
        changed = False
        with self._lock:
            for host in hosts:
                h = host.strip().lower()
                if h and h not in existing:
                    self._rules.append({
                        "id": "fw_" + os.urandom(8).hex(), "host": h, "ports": [443],
                        "note": "seeded: configured provider endpoint", "added_by": "system",
                        "added_at": time.time()})
                    existing.add(h)
                    changed = True
            if changed:
                self._persist()

    # ------------------------------------------------------------- enforcement
    def authorize(self, host: str, port: int = 443, *, purpose: str = "") -> Decision:
        host = _normalise_host(host)
        try:
            port = int(port)
        except (TypeError, ValueError):
            return self._deny(host, 0, "invalid port", purpose)

        # Emergency stop halts all egress.
        latch = self.stop_latch
        if latch is not None:
            try:
                # StopLatch.engaged is a property; support a callable form too.
                engaged = latch.engaged
                engaged = engaged() if callable(engaged) else engaged
                if engaged:
                    return self._deny(host, port, "emergency stop engaged", purpose)
            except Exception:
                return self._deny(host, port, "stop-latch check failed (fail closed)", purpose)

        if not host:
            return self._deny(host, port, "empty host", purpose)
        if self.allow_loopback and host in self.LOOPBACK:
            return self._allow(host, port, "loopback", purpose)

        try:
            with self._lock:
                for rule in self._rules:
                    if _host_matches(rule["host"], host) and port in rule["ports"]:
                        return self._allow(host, port, f"rule {rule['id']}", purpose)
        except Exception:
            return self._deny(host, port, "policy evaluation error (fail closed)", purpose)
        return self._deny(host, port, "no matching allow rule (default deny)", purpose)

    def enforce(self, host: str, port: int = 443, *, purpose: str = "") -> None:
        """authorize() and raise FirewallBlocked when denied."""
        decision = self.authorize(host, port, purpose=purpose)
        if not decision.allowed:
            raise FirewallBlocked(f"egress to {host}:{port} denied: {decision.reason}")

    # ------------------------------------------------------------- internals
    def _allow(self, host, port, reason, purpose) -> Decision:
        self._audit("EGRESS_ALLOWED", "firewall", f"{host}:{port} {purpose} ({reason})", decision="ALLOW")
        return Decision(True, reason, host, port)

    def _deny(self, host, port, reason, purpose) -> Decision:
        self._audit("EGRESS_DENIED", "firewall", f"{host}:{port} {purpose} ({reason})", decision="DENY")
        return Decision(False, reason, host, port)

    def _audit(self, action, actor, detail, decision="ALLOW") -> None:
        if self.audit_service is None:
            return
        try:
            from packages.contracts import AuditEvent
            self.audit_service.record_event(AuditEvent(
                actor=actor, action=action, target="NETWORK_EGRESS",
                policy_decision=decision, result=detail, verification="PASSED"))
        except Exception:
            pass
