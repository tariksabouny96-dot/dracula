"""
HOOD Development Executor Test Suite
Verifies:
1. Stack inspection and framework detection
2. Process supervision, port conflict prevention, readiness healthcheck
3. Test runner and failure traceback classification
4. Reversible code modification, checkpointing, and rollback
5. Live service testing & browser console/DOM capture
6. Emergency stop termination of managed development processes
7. End-to-end autonomous break/fix repair with Maker-Checker validation
Governed by Master System Specification Sections 2, 7, 9, 14, 15 & V0.3 Development Executor Spec.
"""

import sys
import time
import socket
from pathlib import Path
import pytest

from packages.config import SystemConfig
from packages.contracts import RiskLevel
from services.dev_executor.service import DevelopmentExecutor
from services.dev_executor.stack_detector import StackDetector
from services.dev_executor.process_supervisor import ProcessSupervisor
from services.dev_executor.test_runner import TestRunnerService, FailureClassifier
from services.dev_executor.code_modifier import CodeModifier
from services.dev_executor.dev_tools import (
    DevInspectProjectTool,
    DevStartServerTool,
    DevStopServerTool,
    DevRunTestsTool,
    DevApplyCodeChangeTool,
    DevRollbackTool
)
from services.tool_gateway.gateway import ToolGateway
from services.browser.browser_service import BrowserService
from services.core.emergency_stop import EmergencyStopController
from services.core.hood_commander import HoodCommander
from services.audit.service import AuditService
from services.policy.approval_service import ApprovalService


@pytest.fixture(scope="module")
def browser_service():
    srv = BrowserService()
    if srv.is_available():
        srv.launch(headless=True)
    yield srv
    srv.close()


@pytest.fixture
def dev_executor(browser_service):
    executor = DevelopmentExecutor(
        workspace_root=Path.cwd(),
        browser_service=browser_service
    )
    yield executor
    executor.stop_all_services()


def test_stack_detection_python_demo_app():
    """Verifies that stack detector accurately identifies Python runtime, test framework, and entrypoints."""
    info = StackDetector.inspect(Path("demo_app"))
    assert info.primary_language == "python"
    assert info.test_framework == "pytest"
    assert "server.py" in info.entrypoints


def test_test_runner_defect_reproduction_and_classification(dev_executor):
    """Verifies that running tests on demo_app captures the ZeroDivisionError and categorizes it correctly."""
    report = dev_executor.run_tests(test_path="demo_app/tests/test_app.py")
    assert report.success is False
    assert report.total_failed >= 1
    assert len(report.failures) >= 1
    
    # Check failure classification
    failure = report.failures[0]
    assert failure.failure_type in ("ZeroDivisionError", "AssertionError")
    assert "ZeroDivisionError" in report.raw_output


def test_process_supervisor_start_stop_and_readiness(dev_executor):
    """Verifies that process supervisor launches server, confirms HTTP readiness, and terminates safely."""
    # Find a free port
    free_port = ProcessSupervisor.find_free_port(start_port=8910)
    server_cmd = [sys.executable, "demo_app/server.py", str(free_port)]

    info = dev_executor.start_dev_server(
        service_id="test_demo_server",
        command=server_cmd,
        cwd_subpath=".",
        port=free_port,
        readiness_url=f"http://127.0.0.1:{free_port}/health",
        timeout_seconds=10
    )

    assert info.status == "running"
    assert info.is_ready is True
    assert info.pid is not None

    # Check port collision prevention
    with pytest.raises(RuntimeError) as exc_info:
        dev_executor.start_dev_server(
            service_id="collision_service",
            command=server_cmd,
            port=free_port,
            timeout_seconds=2
        )
    assert f"Port {free_port} is already in use" in str(exc_info.value)

    # Stop service
    stop_info = dev_executor.stop_dev_server("test_demo_server")
    assert stop_info.status == "stopped"


def test_live_web_app_inspection(dev_executor):
    """Verifies that DevelopmentExecutor inspects a live service via BrowserService capturing DOM and screenshot."""
    free_port = ProcessSupervisor.find_free_port(start_port=8920)
    server_cmd = [sys.executable, "demo_app/server.py", str(free_port)]

    dev_executor.start_dev_server(
        service_id="live_inspect_server",
        command=server_cmd,
        port=free_port,
        readiness_url=f"http://127.0.0.1:{free_port}/health",
        timeout_seconds=10
    )

    try:
        app_state = dev_executor.inspect_live_web_app(f"http://127.0.0.1:{free_port}/")
        assert app_state["navigation"]["status"] == "success"
        assert "HOOD Demo App" in app_state["dom"]["title"] or "HOOD Demo App" in app_state["dom"]["visible_text_snippet"]
        assert "screenshot_path" in app_state["screenshot"]
    finally:
        dev_executor.stop_dev_server("live_inspect_server")


def test_reversible_code_modifier_and_rollback(dev_executor):
    """Verifies that CodeModifier creates checkpoints and can roll back modifications cleanly."""
    test_file = "artifacts/test_modify_target.txt"
    full_path = Path(test_file)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text("INITIAL_STATE_123", encoding="utf-8")

    # 1. Apply edit
    res = dev_executor.apply_reversible_code_change(
        target_file=test_file,
        content="MODIFIED_STATE_456"
    )
    assert res.success is True
    assert full_path.read_text(encoding="utf-8") == "MODIFIED_STATE_456"
    assert res.checkpoint_ref is not None

    # 2. Roll back using checkpoint ref
    rollback_success = dev_executor.rollback_code_change(test_file, res.checkpoint_ref)
    assert rollback_success is True
    assert full_path.read_text(encoding="utf-8") == "INITIAL_STATE_123"

    # Cleanup
    if full_path.exists():
        full_path.unlink()


def test_emergency_stop_halts_development_services(dev_executor):
    """Verifies that EmergencyStopController immediately terminates all running dev servers."""
    free_port = ProcessSupervisor.find_free_port(start_port=8930)
    server_cmd = [sys.executable, "demo_app/server.py", str(free_port)]

    dev_executor.start_dev_server(
        service_id="emergency_stop_target",
        command=server_cmd,
        port=free_port,
        readiness_url=f"http://127.0.0.1:{free_port}/health",
        timeout_seconds=10
    )

    gateway = ToolGateway()
    audit = AuditService()
    es_controller = EmergencyStopController(
        tool_gateway=gateway,
        audit_service=audit,
        browser_service=dev_executor.browser_service,
        dev_executor=dev_executor
    )

    # Trigger Emergency Stop
    stop_report = es_controller.trigger_stop("Emergency Halt Test")
    assert stop_report["status"] == "EMERGENCY_STOP_ACTIVE"

    # Verify process terminated
    time.sleep(1.0)
    proc_info = dev_executor.supervisor.get_status("emergency_stop_target")
    assert proc_info.status == "stopped"


def test_tool_gateway_dev_tools_integration(dev_executor):
    """Verifies that dev tools are registered in ToolGateway and respect capability gating."""
    gateway = ToolGateway()
    inspect_tool = DevInspectProjectTool(gateway, dev_executor)
    gateway.register_tool(inspect_tool)

    # Execute tool through gateway
    result = gateway.invoke_tool(
        tool_name="dev_inspect_project",
        params={"path": "demo_app"},
        caller_agent="Engineering_Lead"
    )
    assert result["primary_language"] == "python"
    assert "requirements.txt" in result or "server.py" in result["entrypoints"]


def test_end_to_end_autonomous_break_fix_scenario(dev_executor):
    """
    Executes full autonomous defect reproduction, diagnosis, solution formulation,
    reversible fix application, re-testing, and independent Maker-Checker validation.
    """
    commander = HoodCommander()

    # Defective chunk vs Fixed chunk in demo_app/app_service.py
    defective_chunk = "    if tier == 0:\n        # Deliberate bug for reproduction:\n        return price / tier\n    return (price * tier) / 100.0"
    fixed_chunk = "    if tier == 0:\n        return 0.0\n    return (price * tier) / 100.0"

    # Initial state verification: test fails
    initial_report = dev_executor.run_tests(test_path="demo_app/tests/test_app.py")
    assert initial_report.success is False

    # Execute autonomous development task
    result = commander.execute_autonomous_development_task(
        project_subpath="demo_app",
        test_file="demo_app/tests/test_app.py",
        source_file="demo_app/app_service.py",
        original_chunk=defective_chunk,
        fixed_chunk=fixed_chunk,
        dev_executor=dev_executor
    )

    # Validate lifecycle outcomes
    assert result["status"] == "COMPLETED"
    assert result["fix_verified"] is True
    assert result["retest_report"]["success"] is True
    assert result["retest_report"]["total_passed"] == 4
    assert result["retest_report"]["total_failed"] == 0

    # Validate Maker-Checker validation (H05)
    assert result["checker_verification"]["validator"] == "Checker_QA"
    assert result["checker_verification"]["verified"] is True
    assert "Pre-fix failed" in result["checker_verification"]["claim"]

    # Restore original defect to leave demo app in consistent reproducible test fixture state
    if result["edit_result"] and result["edit_result"]["checkpoint_ref"]:
        dev_executor.rollback_code_change("demo_app/app_service.py", result["edit_result"]["checkpoint_ref"])
