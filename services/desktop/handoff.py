"""
HOOD Human Verification Detector & Handoff Manager
Detects security barriers (CAPTCHA, MFA, OTP, 3-D Secure, Biometrics)
without attempting to crack or bypass them. Preserves workflow branches,
notifies Zak, and allows parallel independent tasks to continue.
Governed by Directive Sections 14, 15, 16, 17.
"""

from __future__ import annotations
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from services.desktop.contracts import (
    VerificationChallenge,
    VerificationChallengeType,
    HumanVerificationState,
    ScreenObservation,
    UIElementInfo
)
from services.audit.service import AuditService


class HumanVerificationDetector:
    """Detects security challenges and anti-automation barriers in UI or browser pages."""

    CHALLENGE_PATTERNS = {
        VerificationChallengeType.CAPTCHA: [
            r"captcha", r"recaptcha", r"hcaptcha", r"cf-turnstile", r"robot",
            r"verify you are human", r"vrifiez que vous tes humain", r"geetest", r"arkoselabs"
        ],
        VerificationChallengeType.MFA_OTP: [
            r"enter code", r"one-time password", r"authenticator", r"sms code",
            r"2-step verification", r"two-factor", r"saisissez le code"
        ],
        VerificationChallengeType.THREE_D_SECURE: [
            r"verified by visa", r"mastercard identity check", r"3d secure",
            r"3-d secure", r"authorise payment", r"confirmez le paiement"
        ],
        VerificationChallengeType.BIOMETRIC: [
            r"touch id", r"face id", r"fingerprint", r"windows hello", r"biometric"
        ],
        VerificationChallengeType.SECURITY_KEY: [
            r"security key", r"fido", r"yubikey", r"insert your key"
        ],
        VerificationChallengeType.IDENTITY_CHECK: [
            r"upload id", r"passport", r"driver's license", r"verify your identity"
        ]
    }

    def inspect_text_for_challenge(self, text: str) -> Optional[VerificationChallengeType]:
        if not text:
            return None
        text_lower = text.lower()
        for c_type, patterns in self.CHALLENGE_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, text_lower):
                    return c_type
        return None

    def inspect_observation(self, observation: ScreenObservation) -> Optional[VerificationChallengeType]:
        """Checks active window title, raw text, and UI element names for verification challenges."""
        # 1. Window title
        if observation.active_window and observation.active_window.title:
            detected = self.inspect_text_for_challenge(observation.active_window.title)
            if detected:
                return detected

        # 2. Raw text if available
        if observation.raw_text:
            detected = self.inspect_text_for_challenge(observation.raw_text)
            if detected:
                return detected

        # 3. Discovered elements
        for el in observation.discovered_elements:
            if el.name:
                detected = self.inspect_text_for_challenge(el.name)
                if detected:
                    return detected

        return None


class HumanHandoffManager:
    """
    Manages branches paused for human verification.
    Preserves exact session state and allows independent tasks to proceed.
    """

    def __init__(self, audit_service: Optional[AuditService] = None):
        self.audit_service = audit_service or AuditService()
        self.active_challenges: Dict[str, VerificationChallenge] = {}

    def register_challenge(
        self,
        task_id: str,
        challenge_type: VerificationChallengeType,
        description: str,
        context: Optional[Dict[str, Any]] = None,
        branch_id: str = "main"
    ) -> VerificationChallenge:
        challenge = VerificationChallenge(
            challenge_type=challenge_type,
            task_id=task_id,
            branch_id=branch_id,
            description=description,
            status=HumanVerificationState.WAITING_FOR_HUMAN,
            context_preserved=context or {}
        )
        self.active_challenges[challenge.challenge_id] = challenge

        from packages.contracts import AuditEvent
        self.audit_service.record_event(AuditEvent(
            actor="HumanHandoffManager",
            task_id=task_id,
            action="HUMAN_VERIFICATION_REQUIRED",
            target=challenge_type.value,
            policy_decision="WAIT_FOR_HUMAN",
            result=f"Challenge encountered: {description}. Preserving session state for Zak.",
            verification="PASSED"
        ))
        return challenge

    def get_challenge(self, challenge_id: str) -> Optional[VerificationChallenge]:
        return self.active_challenges.get(challenge_id)

    def list_pending_challenges(self) -> List[VerificationChallenge]:
        return [c for c in self.active_challenges.values() if c.status == HumanVerificationState.WAITING_FOR_HUMAN]

    def mark_completed_by_human(self, challenge_id: str) -> bool:
        """Called when human completion is detected or confirmed."""
        c = self.active_challenges.get(challenge_id)
        if not c:
            return False
        c.status = HumanVerificationState.VERIFIED_BY_HUMAN
        c.resolution_detected_at = datetime.now(timezone.utc)

        from packages.contracts import AuditEvent
        self.audit_service.record_event(AuditEvent(
            actor="Zak",
            task_id=c.task_id,
            action="HUMAN_VERIFICATION_RESOLVED",
            target=c.challenge_type.value,
            policy_decision="RESUME_WORKFLOW",
            result=f"Human verification challenge {challenge_id} resolved. Workflow resuming.",
            verification="PASSED"
        ))
        return True
