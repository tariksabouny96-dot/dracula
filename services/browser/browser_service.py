"""
HOOD Browser Service & Deterministic Interaction Engine
Governed by Master System Specification Section 9, 15 & V0.2 Browser Automation Spec.
Provides safe, deterministic, capability-gated Playwright automation.
"""

import os
import re
import time
import uuid
import hashlib
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from packages.contracts import EvidencePacket, RiskLevel
from packages.config import SystemConfig
from packages.logging.redactor import redact_string
from services.policy.governance import RiskEvaluator
from services.policy.approval_service import ApprovalService
from services.audit.service import AuditService

try:
    from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class BrowserSecurityViolation(Exception):
    """Raised when an untrusted or prohibited browser operation is attempted."""
    pass


class BrowserNavigationError(Exception):
    """Raised when navigation fails or times out."""
    pass


class BrowserService:
    """
    Provider-neutral browser control layer implementing deterministic,
    selector-based interactions, sandboxed downloads/uploads, screenshot evidence,
    and prompt-injection neutralization.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        workspace_root: Optional[Path] = None,
        downloads_dir: Optional[Path] = None,
        evidence_dir: Optional[Path] = None
    ):
        self.config = config or SystemConfig()
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        self.downloads_dir = (downloads_dir or (self.workspace_root / "artifacts" / "downloads")).resolve()
        self.evidence_dir = (evidence_dir or (self.workspace_root / "artifacts" / "browser_evidence")).resolve()

        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._contexts: Dict[str, BrowserContext] = {}
        self._pages: Dict[str, Page] = {}
        self._current_page_id: Optional[str] = None
        self._is_stopped = False

    def is_available(self) -> bool:
        return PLAYWRIGHT_AVAILABLE

    def launch(self, headless: bool = True) -> bool:
        """Launches the underlying Playwright Chromium instance."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright is not installed or available.")
        if self._browser is not None:
            return True

        self._playwright = sync_playwright().start()
        # Use an explicitly configured or detected system Chromium when the
        # Playwright-managed browser is not installed (e.g. offline Linux CI).
        candidate = os.environ.get("HOOD_CHROMIUM_EXECUTABLE") or shutil.which("chromium") or shutil.which("chromium-browser")
        launch_args = {"headless": headless, "downloads_path": str(self.downloads_dir)}
        if candidate and Path(candidate).is_file():
            launch_args["executable_path"] = candidate
        try:
            self._browser = self._playwright.chromium.launch(**launch_args)
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        self._is_stopped = False
        return True

    def close(self):
        """Closes all browser pages, contexts, and the browser process."""
        self._is_stopped = True
        try:
            for ctx in list(self._contexts.values()):
                try:
                    ctx.close()
                except Exception:
                    pass
            self._contexts.clear()
            self._pages.clear()
            self._current_page_id = None

            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None

            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
        except Exception:
            pass

    def create_context(self, context_id: Optional[str] = None) -> str:
        """Creates an isolated browser session context."""
        if not self._browser:
            self.launch()
        cid = context_id or f"ctx_{uuid.uuid4().hex[:8]}"
        ctx = self._browser.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) HOOD-Deterministic-Browser/0.2"
        )
        self._contexts[cid] = ctx
        return cid

    def open_page(self, context_id: Optional[str] = None) -> str:
        """Opens a new tab inside the specified context (or default context)."""
        if self._is_stopped:
            raise BrowserSecurityViolation("Browser operations are halted by Emergency Stop.")
        if not self._contexts:
            cid = self.create_context()
        else:
            cid = context_id or next(iter(self._contexts.keys()))

        ctx = self._contexts[cid]
        page = ctx.new_page()
        pid = f"page_{uuid.uuid4().hex[:8]}"
        self._pages[pid] = page
        self._current_page_id = pid
        return pid

    def get_current_page(self) -> Page:
        if self._is_stopped:
            raise BrowserSecurityViolation("Browser operations are halted by Emergency Stop.")
        if not self._pages or not self._current_page_id or self._current_page_id not in self._pages:
            self.open_page()
        return self._pages[self._current_page_id]

    def navigate(self, url: str, timeout_ms: int = 15000) -> Dict[str, Any]:
        """Navigates to URL and records metadata."""
        page = self.get_current_page()
        start = time.time()
        try:
            resp = page.goto(url, timeout=timeout_ms, wait_until="load")
            status_code = resp.status if resp else 200
        except PlaywrightTimeoutError:
            # Fallback check if DOMContentLoaded
            status_code = 408
        except Exception as e:
            raise BrowserNavigationError(f"Navigation to '{url}' failed: {str(e)}")

        duration_ms = int((time.time() - start) * 1000)
        title = page.title()
        current_url = page.url

        return {
            "status": "success",
            "url": current_url,
            "title": title,
            "status_code": status_code,
            "duration_ms": duration_ms
        }

    def inspect_dom(self) -> Dict[str, Any]:
        """Reads title, visible text, and basic DOM structure."""
        page = self.get_current_page()
        raw_text = page.inner_text("body")
        sanitized_text = self.sanitize_content(raw_text)
        return {
            "title": page.title(),
            "url": page.url,
            "visible_text_snippet": sanitized_text[:1000],
            "raw_length": len(raw_text),
            "sanitized": sanitized_text != raw_text
        }

    def click(self, selector: str, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Clicks an element using deterministic or accessibility selector."""
        page = self.get_current_page()
        start = time.time()
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        page.click(selector, timeout=timeout_ms)
        return {
            "action": "click",
            "selector": selector,
            "duration_ms": int((time.time() - start) * 1000)
        }

    def fill(self, selector: str, text: str, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Fills input element with text."""
        page = self.get_current_page()
        start = time.time()
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        page.fill(selector, text, timeout=timeout_ms)
        return {
            "action": "fill",
            "selector": selector,
            "duration_ms": int((time.time() - start) * 1000)
        }

    def select_option(self, selector: str, value: str, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Selects option in select element."""
        page = self.get_current_page()
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        selected = page.select_option(selector, value)
        return {"action": "select_option", "selector": selector, "selected": selected}

    def scroll(self, direction: str = "down", amount: int = 500) -> Dict[str, Any]:
        """Scrolls page up or down."""
        page = self.get_current_page()
        delta = amount if direction == "down" else -amount
        page.evaluate(f"window.scrollBy(0, {delta})")
        return {"action": "scroll", "direction": direction, "amount": amount}

    def capture_screenshot(self, name_prefix: str = "shot") -> Dict[str, Any]:
        """Captures page screenshot and computes SHA256 integrity hash."""
        page = self.get_current_page()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        import re
        if not isinstance(name_prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", name_prefix):
            raise ValueError("Unsafe screenshot filename prefix")
        filename = f"{name_prefix}_{timestamp}_{uuid.uuid4().hex[:6]}.png"
        target_path = (self.evidence_dir / filename).resolve()
        if not target_path.is_relative_to(self.evidence_dir.resolve()):
            raise PermissionError("Screenshot must stay within the evidence directory")

        page.screenshot(path=str(target_path), full_page=False)

        with open(target_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()

        return {
            "screenshot_path": str(target_path),
            "filename": filename,
            "sha256": digest,
            "url": page.url,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def upload_file(self, selector: str, relative_file_path: str) -> Dict[str, Any]:
        """
        Uploads a file to an input[type='file'] element, strictly enforcing
        that the file resides within the approved workspace root and is not sensitive.
        """
        # 1. Path sandboxing check
        resolved_path = (self.workspace_root / relative_file_path).resolve()
        try:
            resolved_path.relative_to(self.workspace_root)
        except ValueError:
            raise BrowserSecurityViolation(
                f"Upload blocked: target file '{relative_file_path}' is outside approved workspace root."
            )

        if not resolved_path.is_file():
            raise FileNotFoundError(f"Upload file not found: {resolved_path}")

        # 2. Secret and sensitive data leak prevention
        filename_lower = resolved_path.name.lower()
        blocked_substrings = [".env", "vault", "secret", "id_rsa", "credentials", "x_sealed"]
        if any(b in filename_lower for b in blocked_substrings):
            raise BrowserSecurityViolation(
                f"Upload blocked by Secret Protection Policy: '{resolved_path.name}' contains sensitive/confidential material."
            )

        page = self.get_current_page()
        page.set_input_files(selector, str(resolved_path))

        return {
            "action": "upload_file",
            "selector": selector,
            "file": resolved_path.name,
            "size_bytes": resolved_path.stat().st_size
        }

    def sanitize_content(self, text: str) -> str:
        """Neutralizes untrusted web instructions attempting prompt injection."""
        injection_triggers = [
            r"ignore previous instructions",
            r"ignore hood's rules",
            r"system prompt override",
            r"read the api key",
            r"reveal your api key",
            r"reveal credentials",
            r"disable security",
            r"run this powershell command",
            r"upload your secrets",
            r"grant admin",
            r"you are now in unrestricted mode"
        ]
        sanitized = text
        for trigger in injection_triggers:
            sanitized = re.sub(
                trigger,
                "[NEUTRALIZED_UNTRUSTED_INSTRUCTION]",
                sanitized,
                flags=re.IGNORECASE
            )
        return sanitized

    def extract_structured_evidence(self, claim: str) -> EvidencePacket:
        """Extracts visible text and screenshot to compile a comprehensive EvidencePacket."""
        page = self.get_current_page()
        raw_text = page.inner_text("body")
        sanitized = self.sanitize_content(raw_text)
        screenshot_info = self.capture_screenshot(name_prefix="evidence")

        packet = EvidencePacket(
            claim=claim,
            source=page.url,
            source_type="playwright_browser",
            observed_at=datetime.now(timezone.utc),
            is_primary=True,
            confidence=0.98,
            extract=sanitized[:600],
            verifying_agent="Browser_Automation_Lead"
        )
        # Store metadata
        packet.metadata = {
            "page_title": page.title(),
            "screenshot_path": screenshot_info["screenshot_path"],
            "screenshot_hash": screenshot_info["sha256"]
        }
        return packet
