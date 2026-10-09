"""
HOOD Constitutional Governance & Risk Evaluation Engine
Governed by Master System Specification Section 2 & Appendix A.
"""

from typing import Dict, Any, List, Optional
from packages.contracts import RiskLevel, ApprovalRequest, ApprovalStatus
from packages.config import SystemConfig


class RiskEvaluator:
    """Evaluates risk levels and determines whether an action requires explicit human approval."""

    @staticmethod
    def assess_risk(
        action_type: str,
        target: str,
        context: Optional[Dict[str, Any]] = None,
        config: Optional[SystemConfig] = None
    ) -> RiskLevel:
        ctx = context or {}
        is_prod = ctx.get("is_production", False) or "production" in target.lower()
        is_destructive = ctx.get("is_destructive", False) or any(d in action_type.lower() for d in ["delete", "drop", "truncate", "rm", "destroy"])
        is_financial = ctx.get("is_financial", False) or any(f in action_type.lower() for f in ["buy", "purchase", "subscribe", "pay"])
        is_external_comm = ctx.get("is_external_comm", False) or any(c in action_type.lower() for c in ["send_email", "publish", "post", "commit_external"])
        is_security_scope = ctx.get("is_security", False) or any(s in action_type.lower() for s in ["grant_admin", "change_firewall", "change_policy", "exploit"])
        is_consequential_browser = ctx.get("is_consequential_browser", False) or any(b in action_type.lower() for b in ["browser_submit", "submit_form", "browser_upload", "submit_application"])

        # L5: Irreversible / extreme blast radius
        if is_destructive and is_prod:
            return RiskLevel.L5
        if is_destructive and ctx.get("affects_system_wide", False):
            return RiskLevel.L5

        # L4: Security, money, credentials, production data
        if is_financial or is_security_scope or (is_prod and is_destructive):
            return RiskLevel.L4
        if "credential" in action_type.lower() or "secret" in action_type.lower():
            return RiskLevel.L4

        # L3: External communications, production deploy, consequential action, browser consequential submit
        if is_prod or is_external_comm or is_destructive or is_consequential_browser:
            return RiskLevel.L3

        # L2: Controlled local system modification (e.g. installing dependencies, creating directories, applying code changes)
        if any(m in action_type.lower() for m in ["write_file", "modify_config", "install_pkg", "git_commit", "dev_apply_code_change", "dev_start_server", "dev_stop_server", "dev_rollback"]):
            return RiskLevel.L2

        # L1: Safe reversible local action (e.g. read file, run unit test, check status, inspect project)
        if any(r in action_type.lower() for r in ["run_test", "check", "verify", "diff", "dev_inspect_project", "dev_run_tests"]):
            return RiskLevel.L1

        # L0: Observe / read / public lookup
        return RiskLevel.L0

    @staticmethod
    def requires_approval(risk_level: RiskLevel, config: Optional[SystemConfig] = None) -> bool:
        """Determines if the given risk level mandates an approval gate."""
        if risk_level in (RiskLevel.L3, RiskLevel.L4, RiskLevel.L5):
            return True
        if risk_level == RiskLevel.L2:
            # Policy-controlled
            return False
        return False
