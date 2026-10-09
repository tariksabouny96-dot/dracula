"""
HOOD Desktop Control & Financial Governance Contracts
Governed by Master System Specification Sections 2, 8, 9, 15 and Milestone V0.5D Directive.
"""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field

from packages.contracts import RiskLevel, ApprovalStatus


# ==============================================================================
# 1. Desktop & UI Automation Enums
# ==============================================================================

class DesktopControlMethod(str, Enum):
    API = "API"
    PLAYWRIGHT = "PLAYWRIGHT"
    UI_AUTOMATION = "UI_AUTOMATION"
    VISION = "VISION"
    MOUSE_KEYBOARD = "MOUSE_KEYBOARD"
    HUMAN_HANDOFF = "HUMAN_HANDOFF"


class UIElementRole(str, Enum):
    WINDOW = "WINDOW"
    BUTTON = "BUTTON"
    TEXT_FIELD = "TEXT_FIELD"
    CHECKBOX = "CHECKBOX"
    MENU = "MENU"
    MENU_ITEM = "MENU_ITEM"
    TAB = "TAB"
    LIST = "LIST"
    DIALOG = "DIALOG"
    FILE_PICKER = "FILE_PICKER"
    SCROLL_CONTAINER = "SCROLL_CONTAINER"
    LINK = "LINK"
    IMAGE = "IMAGE"
    PANE = "PANE"
    UNKNOWN = "UNKNOWN"


class WindowState(str, Enum):
    NORMAL = "NORMAL"
    MINIMIZED = "MINIMIZED"
    MAXIMIZED = "MAXIMIZED"
    HIDDEN = "HIDDEN"


class MouseButton(str, Enum):
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    MIDDLE = "MIDDLE"


class KeyModifier(str, Enum):
    CTRL = "CTRL"
    ALT = "ALT"
    SHIFT = "SHIFT"
    WIN = "WIN"


# ==============================================================================
# 2. Desktop Entities & Contracts
# ==============================================================================

class WindowInfo(BaseModel):
    hwnd: int
    title: str
    process_id: int
    process_name: str
    class_name: str
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)  # (left, top, right, bottom)
    is_visible: bool = True
    is_active: bool = False
    window_state: WindowState = WindowState.NORMAL


class UIElementInfo(BaseModel):
    element_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    hwnd: int = 0
    role: UIElementRole = UIElementRole.UNKNOWN
    name: str = ""
    value: Optional[str] = None
    bounding_box: Tuple[int, int, int, int] = (0, 0, 0, 0)  # (left, top, right, bottom)
    is_enabled: bool = True
    is_focusable: bool = True
    is_visible: bool = True
    class_name: str = ""
    control_type: str = ""
    children: List[UIElementInfo] = Field(default_factory=list)


class ScreenObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    screen_width: int
    screen_height: int
    active_window: Optional[WindowInfo] = None
    screenshot_path: Optional[str] = None
    image_hash_sha256: str = ""
    discovered_elements: List[UIElementInfo] = Field(default_factory=list)
    has_redacted_secrets: bool = False
    redaction_count: int = 0
    raw_text: Optional[str] = None


class DesktopActionResult(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    action_type: str
    control_method: DesktopControlMethod
    target_description: str
    risk_level: RiskLevel
    success: bool
    details: Dict[str, Any] = Field(default_factory=dict)
    before_state_hash: Optional[str] = None
    after_state_hash: Optional[str] = None
    verification_passed: bool = False
    retry_count: int = 0
    error_message: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ==============================================================================
# 3. Human Verification Enums & Models
# ==============================================================================

class VerificationChallengeType(str, Enum):
    CAPTCHA = "CAPTCHA"
    MFA_OTP = "MFA_OTP"
    THREE_D_SECURE = "THREE_D_SECURE"
    BIOMETRIC = "BIOMETRIC"
    SECURITY_KEY = "SECURITY_KEY"
    IDENTITY_CHECK = "IDENTITY_CHECK"
    UNKNOWN = "UNKNOWN"


class HumanVerificationState(str, Enum):
    NONE = "NONE"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    VERIFIED_BY_HUMAN = "VERIFIED_BY_HUMAN"
    CANCELLED = "CANCELLED"


class VerificationChallenge(BaseModel):
    challenge_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    challenge_type: VerificationChallengeType
    task_id: str
    branch_id: str = "main"
    description: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: HumanVerificationState = HumanVerificationState.WAITING_FOR_HUMAN
    context_preserved: Dict[str, Any] = Field(default_factory=dict)
    resolution_detected_at: Optional[datetime] = None


# ==============================================================================
# 4. Financial Constitution Contracts
# ==============================================================================

class PriceFreshness(str, Enum):
    REFERENCE_PRICE = "REFERENCE_PRICE"
    LIVE_PRICE = "LIVE_PRICE"
    USER_CONFIGURED_PRICE = "USER_CONFIGURED_PRICE"
    UNKNOWN_PRICE = "UNKNOWN_PRICE"


class BillingType(str, Enum):
    ONE_TIME = "ONE_TIME"
    PER_HOUR = "PER_HOUR"
    PER_TOKEN = "PER_TOKEN"
    MONTHLY_SUBSCRIPTION = "MONTHLY_SUBSCRIPTION"
    USAGE_BASED = "USAGE_BASED"


class FinancialOption(BaseModel):
    option_name: str
    cost_usd: float
    billing_type: BillingType
    recurring: bool = False
    quality_score: float  # 0 to 100
    estimated_time_minutes: float
    price_freshness: PriceFreshness = PriceFreshness.REFERENCE_PRICE
    description: str = ""
    assumptions: str = ""


class FinancialRecommendation(BaseModel):
    recommendation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    need_description: str
    recommended_option: str
    expected_cost_usd: float
    currency: str = "USD"
    maximum_possible_cost_usd: float
    billing_type: BillingType
    recurring: bool = False
    price_freshness: PriceFreshness = PriceFreshness.REFERENCE_PRICE
    expected_quality_score: float  # 0 to 100
    expected_time_minutes: float
    expected_benefit: str
    risk: str
    confidence: float  # 0.0 to 1.0
    assumptions: str = ""
    alternatives: List[FinancialOption] = Field(default_factory=list)
    free_alternative: FinancialOption
    consequence_of_free: str
    reason_for_recommendation: str
    approval_required: bool = True
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    authorized_amount_usd: Optional[float] = None
    actual_cost_usd: float = 0.0
    provider: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ==============================================================================
# 5. Overnight & Unattended Execution Contracts
# ==============================================================================

class UnattendedBranchState(str, Enum):
    RUNNING = "RUNNING"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_RESOURCE = "WAITING_FOR_RESOURCE"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class MorningReport(BaseModel):
    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    period_start: datetime
    period_end: datetime
    completed_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    failed_tasks: List[Dict[str, Any]] = Field(default_factory=list)
    waiting_for_approval: List[Dict[str, Any]] = Field(default_factory=list)
    waiting_for_human_verification: List[Dict[str, Any]] = Field(default_factory=list)
    cost_incurred_usd: float = 0.0
    cost_recommended_not_authorized_usd: float = 0.0
    free_fallbacks_used: List[Dict[str, Any]] = Field(default_factory=list)
    security_events: List[Dict[str, Any]] = Field(default_factory=list)
    model_usage_summary: Dict[str, Any] = Field(default_factory=dict)
    important_findings: List[str] = Field(default_factory=list)
