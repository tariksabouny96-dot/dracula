"""
HOOD Development Executor Package
Governed by Master System Specification Section 9, 14, 15 & V0.3 Development Executor Spec.
"""

from .service import DevelopmentExecutor
from .stack_detector import StackDetector, ProjectStackInfo
from .process_supervisor import ProcessSupervisor, ManagedProcessInfo
from .test_runner import TestRunnerService, TestRunReport, TestFailureDetail
from .code_modifier import CodeModifier, CodeEditResult

__all__ = [
    "DevelopmentExecutor",
    "StackDetector",
    "ProjectStackInfo",
    "ProcessSupervisor",
    "ManagedProcessInfo",
    "TestRunnerService",
    "TestRunReport",
    "TestFailureDetail",
    "CodeModifier",
    "CodeEditResult"
]
