import pytest
from pathlib import Path
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.tool_gateway.tools import FSReadFileTool, FSWriteFileTool, CheckpointTool
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService

def test_sandbox_path_validation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateway = ToolGateway(workspace_root=workspace)

    # Valid inside workspace
    p = gateway.validate_path("sub/file.txt")
    assert str(p).startswith(str(workspace))

    # Path traversal attempt outside workspace
    with pytest.raises(PermissionDeniedError):
        gateway.validate_path("../../windows/system32/cmd.exe")

def test_checkpoint_create_and_restore(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_file = workspace / "critical_data.txt"
    target_file.write_text("Original safe state", encoding="utf-8")

    gateway = ToolGateway(workspace_root=workspace)
    chk_tool = CheckpointTool(gateway, checkpoint_dir=workspace / "checkpoints")

    # 1. Create checkpoint
    res = chk_tool.execute({"action": "create", "path": "critical_data.txt"})
    assert res["status"] == "created"
    chk_ref = res["checkpoint_ref"]

    # 2. Simulate destructive change
    target_file.write_text("Corrupted state after bad execution", encoding="utf-8")
    assert target_file.read_text(encoding="utf-8") == "Corrupted state after bad execution"

    # 3. Rollback
    restore_res = chk_tool.execute({"action": "restore", "path": "critical_data.txt", "checkpoint_ref": chk_ref})
    assert restore_res["status"] == "restored"
    assert target_file.read_text(encoding="utf-8") == "Original safe state"
