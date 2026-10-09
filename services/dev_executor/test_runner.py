"""
HOOD Development Executor - Test Runner & Failure Classifier
Executes test suites (pytest, npm test, unittest) and parses failures into structured diagnostics:
Failure Type, Failing File, Line Number, Root Cause Traceback, and Actionable Context.
Governed by Master System Specification Sections 9, 15 & V0.3 Development Executor Spec.
"""

import re
import sys
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class TestFailureDetail(BaseModel):
    test_id: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    failure_type: str  # "AssertionError", "ImportError", "SyntaxError", "TypeError", "Timeout", "HTTPError", "Unknown"
    message: str
    traceback_snippet: str


class TestRunReport(BaseModel):
    command: str
    exit_code: int
    success: bool
    total_passed: int = 0
    total_failed: int = 0
    total_errors: int = 0
    duration_seconds: float = 0.0
    failures: List[TestFailureDetail] = Field(default_factory=list)
    raw_output: str


class FailureClassifier:
    """Classifies traceback patterns into standardized error categories."""

    PATTERNS = [
        (re.compile(r"ImportError:\s*(.*)", re.I), "ImportError"),
        (re.compile(r"ModuleNotFoundError:\s*(.*)", re.I), "ImportError"),
        (re.compile(r"SyntaxError:\s*(.*)", re.I), "SyntaxError"),
        (re.compile(r"IndentationError:\s*(.*)", re.I), "SyntaxError"),
        (re.compile(r"AssertionError(?::\s*(.*))?", re.I), "AssertionError"),
        (re.compile(r"TypeError:\s*(.*)", re.I), "TypeError"),
        (re.compile(r"KeyError:\s*(.*)", re.I), "KeyError"),
        (re.compile(r"AttributeError:\s*(.*)", re.I), "AttributeError"),
        (re.compile(r"ZeroDivisionError:\s*(.*)", re.I), "ZeroDivisionError"),
        (re.compile(r"TimeoutError:\s*(.*)", re.I), "Timeout"),
        (re.compile(r"ConnectionRefusedError:\s*(.*)", re.I), "ConnectionError"),
        (re.compile(r"500 Internal Server Error", re.I), "HTTPError_500"),
        (re.compile(r"404 Not Found", re.I), "HTTPError_404")
    ]

    @classmethod
    def classify(cls, output: str) -> List[TestFailureDetail]:
        details: List[TestFailureDetail] = []
        
        # Look for pytest failure blocks: "FAILED tests/path/to/test.py::test_name - Reason"
        failed_lines = re.findall(r"FAILED\s+([^\s:]+)::([^\s]+)(?:\s+-\s+(.*))?", output)
        for path_str, test_name, reason in failed_lines:
            f_type = "AssertionError"
            msg = reason or "Test assertion failed"
            for pat, typ in cls.PATTERNS:
                m = pat.search(output)
                if m:
                    f_type = typ
                    if m.groups() and m.group(1):
                        msg = m.group(1)
                    break
            
            # Find line number
            line_no = None
            line_match = re.search(rf"{re.escape(path_str)}:(\d+)", output)
            if line_match:
                line_no = int(line_match.group(1))

            details.append(TestFailureDetail(
                test_id=f"{path_str}::{test_name}",
                file_path=path_str,
                line_number=line_no,
                failure_type=f_type,
                message=msg.strip(),
                traceback_snippet=output[-1000:].strip()
            ))

        if not details and ("FAILED" in output or "ERROR" in output):
            # Generic fallback if pytest format varies
            for pat, typ in cls.PATTERNS:
                m = pat.search(output)
                if m:
                    details.append(TestFailureDetail(
                        test_id="generic_failure",
                        failure_type=typ,
                        message=m.group(0),
                        traceback_snippet=output[-800:].strip()
                    ))
                    break

        return details


class TestRunnerService:
    """Executes test suites and captures structured diagnostic reports."""
    __test__ = False

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root.resolve()

    def run_tests(
        self,
        command: Optional[List[str]] = None,
        test_path: Optional[str] = None,
        cwd: Optional[Path] = None,
        timeout_seconds: int = 60
    ) -> TestRunReport:
        target_cwd = (cwd or self.workspace_root).resolve()

        if command is None:
            # Default to Python pytest with virtualenv python if present
            py_bin = sys.executable
            target_path = test_path or "tests"
            cmd = [py_bin, "-m", "pytest", target_path, "-v", "--tb=short"]
        else:
            cmd = command

        start_time = sys.modules["time"].time()
        try:
            res = subprocess.run(
                cmd,
                cwd=str(target_cwd),
                capture_output=True,
                text=True,
                timeout=timeout_seconds
            )
            raw_out = res.stdout + "\n" + res.stderr
            exit_code = res.returncode
        except subprocess.TimeoutExpired:
            raw_out = f"Test execution timed out after {timeout_seconds} seconds"
            exit_code = 124
        except Exception as e:
            raw_out = f"Failed to execute test command: {str(e)}"
            exit_code = 1

        duration = round(sys.modules["time"].time() - start_time, 2)

        # Parse counts from pytest output
        passed_m = re.search(r"(\d+)\s+passed", raw_out)
        failed_m = re.search(r"(\d+)\s+failed", raw_out)
        error_m = re.search(r"(\d+)\s+error", raw_out)

        passed_count = int(passed_m.group(1)) if passed_m else 0
        failed_count = int(failed_m.group(1)) if failed_m else 0
        error_count = int(error_m.group(1)) if error_m else 0

        failures = FailureClassifier.classify(raw_out) if exit_code != 0 else []

        return TestRunReport(
            command=" ".join(cmd),
            exit_code=exit_code,
            success=(exit_code == 0),
            total_passed=passed_count,
            total_failed=failed_count,
            total_errors=error_count,
            duration_seconds=duration,
            failures=failures,
            raw_output=raw_out
        )
