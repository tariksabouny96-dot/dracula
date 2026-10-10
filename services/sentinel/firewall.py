"""
PROJECT SENTINEL — HOOD Firewall Manager
Interfaces with Windows Defender Firewall (read-only by default), inspects
listening ports, audits baseline differences, and reports security telemetry.
Governed by Master System Specification v1.3 Section 12.
"""

from __future__ import annotations
import subprocess
import re
import socket
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from services.sentinel.contracts import FirewallTelemetry, FirewallProfileStatus


class FirewallManager:
    """Manages Windows Defender Firewall inspections, listening ports audit, and baseline deviation detection."""

    # Approved default baseline for HOOD local runtime
    APPROVED_HOOD_PORTS = {8990, 8991, 8992, 8993, 8994, 8995, 8996, 8997, 8998, 8999}
    APPROVED_SYSTEM_PORTS = {135, 445, 5040}  # Core Windows RPC / SMB local endpoints

    def __init__(self, read_only: bool = True):
        self.read_only = read_only

    def inspect_firewall_status(self) -> FirewallTelemetry:
        """Inspects real host firewall state via netsh advfirewall and netstat."""
        profiles = []
        is_active = False

        try:
            # Query netsh advfirewall state
            res = subprocess.run(
                ["netsh", "advfirewall", "show", "allprofiles", "state"],
                capture_output=True,
                timeout=5
            )
            if res.returncode != 0:
                raise RuntimeError(f"Firewall inspection returned exit status {res.returncode}")
            raw = res.stdout.decode("utf-8", errors="replace") if res.stdout else ""

            # Parse domain, private, public profiles
            # Note: handle both French and English Windows locales
            for prof_type in ["domaine", "domain", "privé", "private", "public"]:
                m = re.search(rf"{prof_type}.*?[:\-]\s*\n.*?(État|State)\s+([^\n\r]+)", raw, re.IGNORECASE)
                if m:
                    state_str = m.group(2).strip()
                    is_on = state_str.lower() in ("actif", "on", "active", "habilitado")
                    profiles.append(FirewallProfileStatus(
                        profile_name=prof_type.capitalize(),
                        is_active=is_on,
                        state_raw=state_str
                    ))

            if not profiles:
                raise RuntimeError("Could not verify any Windows firewall profile state")
            is_active = all(profile.is_active for profile in profiles)
        except Exception as e:
            profiles.append(FirewallProfileStatus(profile_name="InspectionError", is_active=False, state_raw=str(e)))
            is_active = False

        # Inspect listening ports
        listening_ports = self.inspect_listening_ports()
        unexpected = []
        deviations = []

        for p_info in listening_ports:
            port = p_info["port"]
            # Any non-approved port listening on 0.0.0.0 is flagged as potentially exposed
            if p_info["address"] in ("0.0.0.0", "::") and port not in self.APPROVED_HOOD_PORTS and port not in self.APPROVED_SYSTEM_PORTS:
                # Dynamic Windows ephemeral ports (>49152) are normal RPC/WMI endpoints but noted
                if port < 49152:
                    unexpected.append(port)
                    deviations.append(f"Unexpected service listening on 0.0.0.0:{port} (PID: {p_info['pid']})")

        return FirewallTelemetry(
            is_active=is_active,
            profiles=profiles,
            listening_ports=listening_ports,
            unexpected_exposed_ports=unexpected,
            baseline_deviations=deviations,
            mode="READ-ONLY" if self.read_only else "ACTIVE-MANAGED",
            last_inspected=datetime.now(timezone.utc).isoformat()
        )

    def inspect_listening_ports(self) -> List[Dict[str, Any]]:
        """Parses netstat -ano for TCP listening sockets."""
        listening = []
        try:
            res = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                timeout=5
            )
            raw = res.stdout.decode("utf-8", errors="replace") if res.stdout else ""
            for line in raw.splitlines():
                line = line.strip()
                if "LISTENING" in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        proto, local_addr, foreign_addr, state, pid = parts[0], parts[1], parts[2], parts[3], parts[4]
                        if ":" in local_addr:
                            host_ip, port_str = local_addr.rsplit(":", 1)
                            try:
                                port_int = int(port_str)
                                listening.append({
                                    "protocol": proto,
                                    "address": host_ip,
                                    "port": port_int,
                                    "pid": int(pid)
                                })
                            except ValueError:
                                pass
        except Exception:
            pass
        return listening

    def propose_rule_change(self, action: str, port: int, direction: str = "in") -> Dict[str, Any]:
        """
        Adheres to Section 12 rule:
        Default mode is READ-ONLY. No firewall changes without ZACK's explicit approval.
        Generates a proposal and rollback plan instead of modifying firewall directly.
        """
        rule_name = f"HOOD_DEFENSE_RULE_{direction.upper()}_{port}"
        command = f"netsh advfirewall firewall add rule name=\"{rule_name}\" dir={direction} action={action} protocol=TCP localport={port}"
        rollback = f"netsh advfirewall firewall delete rule name=\"{rule_name}\""

        return {
            "status": "APPROVAL_REQUIRED",
            "message": "Firewall modification proposed. Explicit Root Owner authorization required.",
            "rule_name": rule_name,
            "proposed_command": command,
            "rollback_command": rollback,
            "port": port,
            "action": action,
            "direction": direction
        }
