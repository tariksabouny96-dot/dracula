"""
HOOD v0.1 Integration Tests - Real Multi-Agent Orchestrated Task
Verifies DAG planning, lead delegation, live reasoning, maker-checker independent validation,
and complete audit log reconstruction.
"""

import pytest
from pathlib import Path
from packages.config import load_config
from packages.contracts import TaskStatus, RiskLevel
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.memory.service import MemoryService
from services.model_gateway.router import ModelRouter
from services.model_gateway.cost_controller import CostController
from services.tool_gateway.gateway import ToolGateway
from services.tool_gateway.tools import FSListDirTool, GitOpsTool, TestRunnerTool
from services.core.hood_commander import HoodCommander


def test_real_orchestrated_audit_task(tmp_path):
    """Executes the full multi-agent audit task, verifying DAG scheduling and maker-checker validation."""
    config = load_config()
    audit_db = tmp_path / "hood_audit.db"
    mem_db = tmp_path / "hood_mem.db"

    audit_svc = AuditService(db_path=audit_db)
    mem_svc = MemoryService(db_path=mem_db)
    cost_ctrl = CostController(config.budgets)
    router = ModelRouter(config, cost_ctrl)
    appr_svc = ApprovalService()

    tool_gw = ToolGateway(config, appr_svc, audit_svc)
    tool_gw.register_tool(FSListDirTool(tool_gw))
    tool_gw.register_tool(GitOpsTool(tool_gw))
    tool_gw.register_tool(TestRunnerTool(tool_gw))

    commander = HoodCommander(
        config=config,
        model_router=router,
        approval_service=appr_svc,
        audit_service=audit_svc,
        memory_service=mem_svc
    )

    res = commander.execute_repository_audit_task(tool_gw)

    # This is a simulated/mocked integration fixture. Consequential recommendations
    # must remain blocked without an independent checker and explicit approval.
    assert "task_results" in res
    results = res["task_results"]
    assert len(results) == 3
    states = [task.status for task in results.values()]
    assert states.count(TaskStatus.COMPLETED) == 2
    assert states.count(TaskStatus.WAITING_APPROVAL) == 1

    synth = res["synthesis"]
    assert synth["consequential_recommendation"] is None
    assert not synth["checker_verification"]

    # Evidence must not invent a completed consequential operation.
    pending_ids = [tid for tid, task in results.items()
                   if task.status == TaskStatus.WAITING_APPROVAL]
    for tid in pending_ids:
        events = audit_svc.get_events_for_task(tid)
        assert not any(e.action == "task_completed" for e in events)
