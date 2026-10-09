"""
HOOD Desktop Control & Financial Governance Package Initializer
"""

from services.desktop.contracts import (
    DesktopControlMethod,
    UIElementRole,
    UIElementInfo,
    WindowInfo,
    WindowState,
    MouseButton,
    KeyModifier,
    ScreenObservation,
    DesktopActionResult,
    VerificationChallengeType,
    HumanVerificationState,
    VerificationChallenge,
    PriceFreshness,
    BillingType,
    FinancialOption,
    FinancialRecommendation,
    UnattendedBranchState,
    MorningReport,
)

from services.desktop.windows_backend import DesktopUnavailable, WindowsNativeBackend
from services.desktop.accessibility import AccessibilityEngine
from services.desktop.input_controller import GovernedInputController
from services.desktop.screen_observer import ScreenObserver
from services.desktop.application_manager import ApplicationManager
from services.desktop.handoff import HumanVerificationDetector, HumanHandoffManager
from services.desktop.policy_bridge import FinancialAdvisor, FinancialConstitutionViolationError
from services.desktop.audit import OvernightExecutionManager
from services.desktop.desktop_service import DesktopService

__all__ = [
    "DesktopControlMethod",
    "UIElementRole",
    "UIElementInfo",
    "WindowInfo",
    "WindowState",
    "MouseButton",
    "KeyModifier",
    "ScreenObservation",
    "DesktopActionResult",
    "VerificationChallengeType",
    "HumanVerificationState",
    "VerificationChallenge",
    "PriceFreshness",
    "BillingType",
    "FinancialOption",
    "FinancialRecommendation",
    "UnattendedBranchState",
    "MorningReport",
    "WindowsNativeBackend",
    "DesktopUnavailable",
    "AccessibilityEngine",
    "GovernedInputController",
    "ScreenObserver",
    "ApplicationManager",
    "HumanVerificationDetector",
    "HumanHandoffManager",
    "FinancialAdvisor",
    "FinancialConstitutionViolationError",
    "OvernightExecutionManager",
    "DesktopService",
]
