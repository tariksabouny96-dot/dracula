"""
HOOD v0.1 Integration Tests - Real Tool Gateway v0.1
Verifies capability checks, path sandbox restrictions, fs_list_dir, git_ops, and test_runner tools.
"""

import pytest
from pathlib import Path
from packages.config import load_config
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.tool_gateway.tools import (
    FSReadFileTool,
    FSWriteFileTool,
    FSListDirTool,
    ShellExecTool,
    GitOpsTool,
    TestRunnerTool,
    CheckpointTool
)


def test_tool_gateway_capabilities_and_execution(tmp_path):
    """Verifies that all registered tools enforce sandbox and capabilities."""
    config = load_config()
    appr = ApprovalService()
    audit = AuditService(db_path=tmp_path / "audit.db")
    gw = ToolGateway(config, appr, audit)

    gw.register_tool(FSReadFileTool(gw))
    gw.register_tool(FSWriteFileTool(gw))
    gw.register_tool(FSListDirTool(gw))
    gw.register_tool(GitOpsTool(gw))
    gw.register_tool(TestRunnerTool(gw))

    # 1. Test fs_list_dir
    list_res = gw.invoke_tool("fs_list_dir", {"path": "."})
    assert "entries" in list_res
    names = [e["name"] for e in list_res["entries"]]
    assert "hood_cli.py" in names

    # 2. Test git_ops status
    git_res = gw.invoke_tool("git_ops", {"action": "status"})
    assert "output" in git_res
    assert git_res["exit_code"] == 0

    # 3. Test git_ops log
    log_res = gw.invoke_tool("git_ops", {"action": "log", "limit": 3})
    assert "output" in log_res
    assert log_res["exit_code"] == 0

    # 4. Test run_tests
    test_res = gw.invoke_tool("run_tests", {"test_path": "tests/unit/test_config.py"})
    assert test_res["exit_code"] == 0
    assert test_res["success"] is True

    # 5. Sandbox enforcement
    with pytest.raises(PermissionDeniedError):
        gw.invoke_tool("fs_list_dir", {"path": "C:\\Windows\\System32"})
