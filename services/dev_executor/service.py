"""
HOOD Development Executor - Primary Service
Unifies project stack discovery, dev process supervision, test execution & failure parsing,
browser QA error capture, and reversible code modification under a single interface.
Governed by Master System Specification Sections 9, 14, 15 & V0.3 Development Executor Spec.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from packages.config import SystemConfig
from packages.contracts import EvidencePacket, RiskLevel
from services.dev_executor.stack_detector import StackDetector, ProjectStackInfo
from services.dev_executor.process_supervisor import ProcessSupervisor, ManagedProcessInfo
from services.dev_executor.test_runner import TestRunnerService, TestRunReport
from services.dev_executor.code_modifier import CodeModifier, CodeEditResult
from services.browser.browser_service import BrowserService
from packages.config.paths import store_dir


class DevelopmentExecutor:
    """Primary development execution and local application testing service."""

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        config: Optional[SystemConfig] = None,
        browser_service: Optional[BrowserService] = None
    ):
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.config = config or SystemConfig()
        self.browser_service = browser_service
        self.supervisor = ProcessSupervisor(log_dir=store_dir(None, "artifacts/dev_logs"))
        self.test_runner = TestRunnerService(self.workspace_root)
        self.modifier = CodeModifier(self.workspace_root)

    def inspect_project(self, project_subpath: str = ".") -> ProjectStackInfo:
        """Inspects project structure, languages, manifests, frameworks, and entrypoints."""
        target_path = (self.workspace_root / project_subpath).resolve()
        return StackDetector.inspect(target_path)

    def start_dev_server(
        self,
        service_id: str,
        command: List[str] | str,
        cwd_subpath: str = ".",
        port: Optional[int] = None,
        readiness_url: Optional[str] = None,
        timeout_seconds: int = 15,
        env: Optional[Dict[str, str]] = None
    ) -> ManagedProcessInfo:
        """Starts a supervised background development server with readiness verification."""
        target_cwd = (self.workspace_root / cwd_subpath).resolve()
        return self.supervisor.start_service(
            service_id=service_id,
            command=command,
            cwd=target_cwd,
            port=port,
            readiness_url=readiness_url,
            timeout_seconds=timeout_seconds,
            env=env
        )

    def stop_dev_server(self, service_id: str) -> ManagedProcessInfo:
        """Halts a specific supervised dev server."""
        return self.supervisor.stop_service(service_id)

    def stop_all_services(self) -> List[ManagedProcessInfo]:
        """Halts all supervised processes (used by Emergency Stop and teardown)."""
        return self.supervisor.stop_all()

    def get_service_logs(self, service_id: str, max_lines: int = 100) -> str:
        """Retrieves stdout/stderr output from managed service."""
        return self.supervisor.get_service_logs(service_id, max_lines)

    def run_tests(
        self,
        command: Optional[List[str]] = None,
        test_path: Optional[str] = None,
        cwd_subpath: Optional[str] = None,
        timeout_seconds: int = 60
    ) -> TestRunReport:
        """Runs test suite and classifies failure stack traces."""
        target_cwd = (self.workspace_root / cwd_subpath).resolve() if cwd_subpath else self.workspace_root
        return self.test_runner.run_tests(
            command=command,
            test_path=test_path,
            cwd=target_cwd,
            timeout_seconds=timeout_seconds
        )

    def inspect_live_web_app(self, url: str) -> Dict[str, Any]:
        """
        Interacts with the running local development server using BrowserService to capture
        DOM state, screenshots, console errors, and network issues.
        """
        if not self.browser_service:
            raise RuntimeError("BrowserService is not attached to DevelopmentExecutor.")

        nav_res = self.browser_service.navigate(url)
        dom = self.browser_service.inspect_dom()
        screenshot = self.browser_service.capture_screenshot(name_prefix="app_qa")

        # Extract console logs if page available
        console_errors = []
        try:
            page = self.browser_service.get_current_page()
            # Capture any visible error elements or state
            error_elements = page.query_selector_all(".error, .alert-danger, [role='alert']")
            for el in error_elements:
                console_errors.append(el.inner_text())
        except Exception:
            pass

        return {
            "navigation": nav_res,
            "dom": dom,
            "screenshot": screenshot,
            "detected_error_elements": console_errors
        }

    def apply_reversible_code_change(
        self,
        target_file: str,
        content: str,
        is_patch_replace: bool = False,
        original_chunk: Optional[str] = None,
        replacement_chunk: Optional[str] = None
    ) -> CodeEditResult:
        """Applies an isolated, restorable code change with a checkpoint backup."""
        return self.modifier.apply_targeted_edit(
            target_rel_path=target_file,
            content=content,
            is_patch_replace=is_patch_replace,
            original_chunk=original_chunk,
            replacement_chunk=replacement_chunk
        )

    def rollback_code_change(self, target_file: str, checkpoint_ref: str) -> bool:
        """Restores a file to its checkpointed backup."""
        return self.modifier.restore_file_checkpoint(target_file, checkpoint_ref)
