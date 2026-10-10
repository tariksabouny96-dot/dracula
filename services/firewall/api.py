"""Owner-only HTTP management for the HOOD egress firewall.

The server enforces Host, CSRF, session and permission before these run. Rule
changes additionally require the Root Owner role (the policy layer refuses a
non-owner), so an agent or a compromised lower-privilege session can never widen
what HOOD may reach.
"""
from __future__ import annotations

import os
from pathlib import Path

from ui.routes import SERVICES, route

from .policy import NetworkFirewall


def _firewall() -> NetworkFirewall:
    fw = SERVICES.get("firewall")
    if fw is None:
        data_dir = Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "firewall"
        fw = SERVICES.setdefault("firewall", NetworkFirewall(data_dir))
    return fw


def _is_root_owner(ctx) -> bool:
    role = getattr(ctx.session, "role", None)
    return getattr(role, "value", role) == "ROOT_OWNER"


@route("GET", r"/api/firewall/rules", permission="VIEW_PROJECT_DATA")
def list_rules(ctx):
    return {"default_policy": "deny", "rules": _firewall().list_rules()}


@route("POST", r"/api/firewall/rules", permission="OWNERSHIP_ADMIN")
def add_rule(ctx):
    ctx.require_confirm("add an outbound firewall allow rule")
    host = ctx.payload.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("'host' is required")
    ports = ctx.payload.get("ports") or [443]
    if not isinstance(ports, list):
        raise ValueError("'ports' must be a list of integers")
    try:
        rule = _firewall().allow(host, [int(p) for p in ports], note=str(ctx.payload.get("note", "")),
                                 added_by=ctx.username, is_root_owner=_is_root_owner(ctx))
    except PermissionError as exc:
        raise PermissionError(str(exc))
    return 201, rule


@route("POST", r"/api/firewall/rules/(?P<rule_id>fw_[0-9a-f]{16})/revoke", permission="OWNERSHIP_ADMIN")
def revoke_rule(ctx):
    ctx.require_confirm("revoke a firewall allow rule")
    removed = _firewall().revoke(ctx.match["rule_id"], actor=ctx.username, is_root_owner=_is_root_owner(ctx))
    if not removed:
        raise KeyError(ctx.match["rule_id"])
    return {"revoked": ctx.match["rule_id"]}
