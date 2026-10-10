"""
HOOD Core Tools Implementation v0.1
Safe, typed, sandboxed execution components for controlled local actions.
Governed by Master System Specification Sections 9, 15 & Build Instructions Section 15.
"""

import os
import sys
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from .gateway import BaseTool, PermissionDeniedError, ToolGateway
from packages.config.paths import store_dir


class FSReadFileTool(BaseTool):
    def __init__(self, gateway: ToolGateway):
        super().__init__("fs_read_file", "fs:read", "Reads content from a workspace file")
        self.gateway = gateway

    def execute(self, params: Dict[str, Any]) -> str:
        rel_path = params.get("path")
        if not rel_path:
            raise ValueError("Parameter 'path' is required")
        safe_path = self.gateway.validate_path(rel_path)
        if not safe_path.is_file():
            raise FileNotFoundError(f"File {rel_path} does not exist.")
        return safe_path.read_text(encoding="utf-8")


class FSWriteFileTool(BaseTool):
    def __init__(self, gateway: ToolGateway):
        super().__init__("fs_write_file", "fs:write", "Writes content to a workspace file")
        self.gateway = gateway

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = params.get("path")
        content = params.get("content", "")
        if not rel_path:
            raise ValueError("Parameter 'path' is required")
        safe_path = self.gateway.validate_path(rel_path)
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
        return {"status": "success", "bytes_written": len(content.encode("utf-8")), "path": rel_path}


class FSListDirTool(BaseTool):
    def __init__(self, gateway: ToolGateway):
        super().__init__("fs_list_dir", "fs:list", "Lists directory contents inside approved workspace")
        self.gateway = gateway

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rel_path = params.get("path", ".")
        safe_path = self.gateway.validate_path(rel_path)
        if not safe_path.is_dir():
            raise NotADirectoryError(f"Directory {rel_path} does not exist or is not a directory.")

        entries = []
        for child in safe_path.iterdir():
            try:
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size_bytes": child.stat().st_size if child.is_file() else None
                })
            except Exception:
                pass

        return {
            "path": rel_path,
            "total_items": len(entries),
            "entries": entries[:100]
        }


class ShellExecTool(BaseTool):
    def __init__(self, gateway: ToolGateway):
        super().__init__("shell_exec", "shell:exec", "Executes safe terminal commands")
        self.gateway = gateway

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        cmd = params.get("command")
        if not cmd:
            raise ValueError("Parameter 'command' is required")

        # Executing arbitrary commands is unsafe without an OS-level sandbox,
        # even with a capability and a chat-level approval. Fail closed.
        raise PermissionDeniedError(
            "Shell execution is disabled until a disposable OS sandbox is available"
        )


class GitOpsTool(BaseTool):
    def __init__(self, gateway: ToolGateway):
        super().__init__("git_ops", "git:inspect", "Performs controlled read-only Git operations (status, log, diff)")
        self.gateway = gateway

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        action = params.get("action", "status")
        git_bin = "C:\\Program Files\\Git\\cmd\\git.exe" if os.path.exists("C:\\Program Files\\Git\\cmd\\git.exe") else "git"
        
        if action == "status":
            cmd = [git_bin, "status", "--porcelain"]
        elif action == "log":
            n = int(params.get("limit", 5))
            cmd = [git_bin, "log", f"-n{n}", "--oneline"]
        elif action == "diff":
            target = params.get("target", "")
            cmd = [git_bin, "diff"] + ([target] if target else [])
        else:
            raise ValueError(f"Unsupported Git operation: '{action}'. Only status, log, and diff permitted.")

        res = subprocess.run(cmd, cwd=self.gateway.workspace_root, capture_output=True, text=True, timeout=15)
        return {
            "action": action,
            "exit_code": res.returncode,
            "output": res.stdout.strip(),
            "error": res.stderr.strip()
        }


class TestRunnerTool(BaseTool):
    __test__ = False

    def __init__(self, gateway: ToolGateway):
        super().__init__("run_tests", "test:run", "Executes project pytest suite in virtual environment")
        self.gateway = gateway

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        test_path = params.get("test_path", "tests")
        python_exe = sys.executable

        cmd = [python_exe, "-m", "pytest", test_path, "-v", "--tb=short"]
        res = subprocess.run(cmd, cwd=self.gateway.workspace_root, capture_output=True, text=True, timeout=60)

        # Parse test outcomes
        output = res.stdout + res.stderr
        passed = "passed" in output.lower()
        failed = "failed" in output.lower()

        return {
            "test_path": test_path,
            "exit_code": res.returncode,
            "success": (res.returncode == 0),
            "output_summary": output[-500:].strip() if len(output) > 500 else output.strip()
        }


class CheckpointTool(BaseTool):
    """
    Implements H07: BACKUP -> CHECKPOINT -> EXECUTE -> VERIFY -> ROLLBACK IF NEEDED.
    """
    def __init__(self, gateway: ToolGateway, checkpoint_dir: Optional[Path] = None):
        super().__init__("checkpoint", "checkpoint:manage", "Creates and restores file backups")
        self.gateway = gateway
        self.checkpoint_dir = checkpoint_dir or store_dir(None, "artifacts/checkpoints")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        action = params.get("action", "create")
        target_path = params.get("path")
        if not target_path:
            raise ValueError("Target path is required for checkpoint")

        safe_path = self.gateway.validate_path(target_path)

        if action == "create":
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_file = self.checkpoint_dir / f"chk_{Path(target_path).name}_{timestamp}.bak"
            if safe_path.is_file():
                shutil.copy2(safe_path, backup_file)
                return {"status": "created", "checkpoint_ref": str(backup_file.name), "path": target_path}
            raise FileNotFoundError(f"Target {target_path} not found for backup.")

        elif action == "restore":
            ref = params.get("checkpoint_ref")
            if not ref:
                raise ValueError("checkpoint_ref required for restore")
            backup_file = self.checkpoint_dir / ref
            if not backup_file.is_file():
                raise FileNotFoundError(f"Checkpoint file {ref} not found.")
            shutil.copy2(backup_file, safe_path)
            return {"status": "restored", "checkpoint_ref": ref, "path": target_path}

        raise ValueError(f"Unknown checkpoint action: {action}")


class DesktopControlTool(BaseTool):
    """
    Enforces Tool Gateway capability checking for Desktop Automation.
    Capability: 'desktop:control'
    """
    def __init__(self, gateway: ToolGateway, desktop_service=None):
        super().__init__("desktop_control", "desktop:control", "Executes governed desktop control actions")
        self.gateway = gateway
        self.desktop_service = desktop_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        grant_id = params.get("grant_id")
        if not grant_id or grant_id not in self.gateway.active_grants:
            raise PermissionDeniedError(
                "Execution of 'desktop_control' blocked: Missing valid capability grant for 'desktop:control'."
            )
        grant = self.gateway.active_grants[grant_id]
        if grant.is_revoked or grant.capability != self.required_capability:
            raise PermissionDeniedError(
                f"Execution blocked: Grant '{grant_id}' is invalid or revoked for capability '{self.required_capability}'."
            )

        if not self.desktop_service:
            from services.desktop.desktop_service import DesktopService
            self.desktop_service = DesktopService(config=self.gateway.config, tool_gateway=self.gateway)

        action = params.get("action", "observe")
        task_id = params.get("task_id", "default_task")

        if action == "observe":
            obs = self.desktop_service.observe_screen(capture_image=params.get("capture_image", True))
            return {
                "observation_id": obs.observation_id,
                "screen_width": obs.screen_width,
                "screen_height": obs.screen_height,
                "active_window": obs.active_window.model_dump() if obs.active_window else None,
                "elements_count": len(obs.discovered_elements),
                "screenshot_path": obs.screenshot_path,
                "has_redacted_secrets": obs.has_redacted_secrets
            }
        elif action == "windows":
            wins = self.desktop_service.enumerate_windows()
            return {"windows": [w.model_dump() for w in wins]}
        elif action == "focus":
            hwnd = int(params.get("hwnd", 0))
            ok = self.desktop_service.focus_window(hwnd)
            return {"hwnd": hwnd, "focused": ok}
        elif action == "click":
            hwnd = int(params.get("hwnd", 0))
            role_str = params.get("role")
            from services.desktop.contracts import UIElementRole
            role = UIElementRole(role_str) if role_str else None
            name = params.get("name")
            res = self.desktop_service.execute_semantic_click(task_id, hwnd, role=role, element_name=name)
            return res.model_dump()
        elif action == "type":
            text = params.get("text", "")
            res = self.desktop_service.type_into_active(task_id, text)
            return res.model_dump()
        else:
            raise ValueError(f"Unknown desktop action: {action}")
