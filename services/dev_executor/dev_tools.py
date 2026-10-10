"""
HOOD Development Executor Tools
Integrates DevelopmentExecutor capabilities into ToolGateway.
Governed by Master System Specification Section 9, 15 & V0.3 Development Executor Spec.
"""

from typing import Dict, Any, List, Optional
from pathlib import Path

from services.tool_gateway.gateway import BaseTool, ToolGateway
from services.dev_executor.service import DevelopmentExecutor


class DevInspectProjectTool(BaseTool):
    def __init__(self, gateway: ToolGateway, dev_executor: DevelopmentExecutor):
        super().__init__("dev_inspect_project", "dev:inspect", "Inspects project structure, frameworks, and dependencies")
        self.gateway = gateway
        self.dev_executor = dev_executor

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        subpath = params.get("path", ".")
        info = self.dev_executor.inspect_project(subpath)
        return info.model_dump()


class DevStartServerTool(BaseTool):
    def __init__(self, gateway: ToolGateway, dev_executor: DevelopmentExecutor):
        super().__init__("dev_start_server", "dev:server", "Starts a supervised local development service")
        self.gateway = gateway
        self.dev_executor = dev_executor

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        service_id = params.get("service_id", "dev_server")
        command = params.get("command")
        if not command:
            raise ValueError("Parameter 'command' is required to start dev server.")
        cwd = params.get("cwd", ".")
        port = params.get("port")
        readiness_url = params.get("readiness_url")
        timeout = int(params.get("timeout", 15))

        info = self.dev_executor.start_dev_server(
            service_id=service_id,
            command=command,
            cwd_subpath=cwd,
            port=port,
            readiness_url=readiness_url,
            timeout_seconds=timeout
        )
        return info.model_dump()


class DevStopServerTool(BaseTool):
    def __init__(self, gateway: ToolGateway, dev_executor: DevelopmentExecutor):
        super().__init__("dev_stop_server", "dev:server", "Halts a supervised local development service")
        self.gateway = gateway
        self.dev_executor = dev_executor

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        service_id = params.get("service_id", "dev_server")
        info = self.dev_executor.stop_dev_server(service_id)
        return info.model_dump()


class DevRunTestsTool(BaseTool):
    def __init__(self, gateway: ToolGateway, dev_executor: DevelopmentExecutor):
        super().__init__("dev_run_tests", "dev:test", "Executes test suite and classifies failures")
        self.gateway = gateway
        self.dev_executor = dev_executor

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        test_path = params.get("test_path")
        cwd = params.get("cwd")
        timeout = int(params.get("timeout", 60))
        report = self.dev_executor.run_tests(
            test_path=test_path,
            cwd_subpath=cwd,
            timeout_seconds=timeout
        )
        return report.model_dump()


class DevApplyCodeChangeTool(BaseTool):
    def __init__(self, gateway: ToolGateway, dev_executor: DevelopmentExecutor):
        super().__init__("dev_apply_code_change", "dev:modify", "Applies targeted code modification with checkpoint backup")
        self.gateway = gateway
        self.dev_executor = dev_executor

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target_file = params.get("target_file")
        if not target_file:
            raise ValueError("Parameter 'target_file' is required")
        content = params.get("content", "")
        is_patch_replace = bool(params.get("is_patch_replace", False))
        original_chunk = params.get("original_chunk")
        replacement_chunk = params.get("replacement_chunk")

        res = self.dev_executor.apply_reversible_code_change(
            target_file=target_file,
            content=content,
            is_patch_replace=is_patch_replace,
            original_chunk=original_chunk,
            replacement_chunk=replacement_chunk
        )
        return res.model_dump()


class DevRollbackTool(BaseTool):
    def __init__(self, gateway: ToolGateway, dev_executor: DevelopmentExecutor):
        super().__init__("dev_rollback", "dev:rollback", "Restores a file to its checkpointed backup")
        self.gateway = gateway
        self.dev_executor = dev_executor

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        target_file = params.get("target_file")
        checkpoint_ref = params.get("checkpoint_ref")
        if not target_file or not checkpoint_ref:
            raise ValueError("Parameters 'target_file' and 'checkpoint_ref' are required")

        success = self.dev_executor.rollback_code_change(target_file, checkpoint_ref)
        return {"success": success, "target_file": target_file, "checkpoint_ref": checkpoint_ref}
