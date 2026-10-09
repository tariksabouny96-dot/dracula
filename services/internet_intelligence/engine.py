"""
HOOD Internet Intelligence Engine v0.1
Implements live HTTP retrieval, source classification, freshness metadata,
evidence packet synthesis, and prompt-injection defense.
Governed by Master System Specification Section 7.
"""

import re
import urllib.request
import urllib.error
from html.parser import HTMLParser
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta

from packages.contracts import EvidencePacket


class StaleDataError(Exception):
    """Raised when data freshness policy indicates data is stale and must be revalidated."""
    pass


class PromptInjectionAttempt(Exception):
    """Raised when untrusted external content attempts to commandeer execution."""
    pass


class SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style"):
            self.in_script = True

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style"):
            self.in_script = False

    def handle_data(self, data):
        if not self.in_script:
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)

    def get_text(self) -> str:
        return " ".join(self.text_parts)


class RetrievalMethod(str):
    INTERNAL = "internal_data"
    OFFICIAL_API = "official_api"
    DIRECT_HTTP = "direct_http"
    STRUCTURED_EXTRACTION = "structured_extraction"
    PLAYWRIGHT_BROWSER = "playwright_browser"
    HUMAN_ESCALATION = "human_escalation"


class InternetIntelligenceEngine:
    """
    Manages real web evidence collection, freshness verification,
    prompt injection defense, and dynamic method routing.
    Hierarchy: Direct HTTP -> Structured Extraction -> Playwright Browser -> Human Escalation
    """

    def __init__(self, browser_service=None):
        self.evidence_store: List[EvidencePacket] = []
        self.browser_service = browser_service
        self.method_history: List[Dict[str, Any]] = []

    def classify_source(self, url: str) -> str:
        url_lower = url.lower()
        if "api." in url_lower or "/api/" in url_lower:
            return "official_api"
        if "github.com" in url_lower or "gitlab.com" in url_lower:
            return "code_repository"
        if "docs." in url_lower or "documentation" in url_lower:
            return "official_documentation"
        return "public_web"

    def check_freshness(self, packet: EvidencePacket, max_age_seconds: int = 3600) -> bool:
        """Enforces freshness policy (Scenario A06: Stale pricing or API limits require revalidation)."""
        age = (datetime.now(timezone.utc) - packet.observed_at).total_seconds()
        return age <= max_age_seconds

    def sanitize_untrusted_content(self, raw_content: str) -> str:
        """
        Enforces Section 12.2 & Scenario A08:
        Untrusted content from web pages/APIs cannot grant permissions, invoke tools, or leak secrets.
        """
        injection_triggers = [
            r"ignore previous instructions",
            r"system prompt override",
            r"grant admin",
            r"print secret",
            r"reveal credentials",
            r"you are now in unrestricted mode"
        ]
        for trigger in injection_triggers:
            raw_content = re.sub(
                trigger,
                "[NEUTRALIZED_UNTRUSTED_INSTRUCTION]",
                raw_content,
                flags=re.IGNORECASE
            )

        return raw_content

    def fetch_url(self, url: str, timeout_sec: int = 15) -> Dict[str, Any]:
        """Performs live HTTP retrieval with browser headers and redirects handling."""
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HOOD-Internet-Intelligence/0.1",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                status_code = resp.status
                content_type = resp.headers.get_content_type()
                raw_bytes = resp.read()
                html_text = raw_bytes.decode("utf-8", errors="replace")

            # Extract clean text
            extractor = SimpleHTMLTextExtractor()
            extractor.feed(html_text)
            clean_text = extractor.get_text()

            return {
                "url": url,
                "status_code": status_code,
                "content_type": content_type,
                "raw_html_length": len(html_text),
                "extracted_text": clean_text,
                "retrieved_at": datetime.now(timezone.utc).isoformat()
            }
        except urllib.error.HTTPError as e:
            return {
                "url": url,
                "status_code": e.code,
                "error": f"HTTP Error {e.code}",
                "extracted_text": "",
                "retrieved_at": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            return {
                "url": url,
                "status_code": 0,
                "error": str(e),
                "extracted_text": "",
                "retrieved_at": datetime.now(timezone.utc).isoformat()
            }

    def research_url(
        self,
        url: str,
        claim: str,
        force_browser: bool = False,
        requires_interaction: bool = False
    ) -> EvidencePacket:
        """
        Dynamically chooses the cheapest, safest retrieval method:
        Direct HTTP -> Structured Extraction -> Playwright Browser -> Human Escalation
        """
        selected_method = RetrievalMethod.DIRECT_HTTP
        fallback_occurred = False
        fallback_reason = None

        # Check if browser is strictly required upfront
        if force_browser or requires_interaction:
            selected_method = RetrievalMethod.PLAYWRIGHT_BROWSER

        fetch_result = None
        # 1. Attempt Direct HTTP first if not forced to browser
        if selected_method == RetrievalMethod.DIRECT_HTTP:
            fetch_result = self.fetch_url(url)
            # If HTTP returns an error (403/dynamic JS/bot protection) and browser service is available, escalate
            if fetch_result.get("error") and self.browser_service and self.browser_service.is_available():
                selected_method = RetrievalMethod.PLAYWRIGHT_BROWSER
                fallback_occurred = True
                fallback_reason = f"Direct HTTP failed ({fetch_result.get('error')}); escalating to Playwright browser."

        # 2. Use Playwright if selected or escalated
        if selected_method == RetrievalMethod.PLAYWRIGHT_BROWSER:
            if not self.browser_service or not self.browser_service.is_available():
                # Cannot use browser, record failure
                self.method_history.append({
                    "url": url,
                    "method": RetrievalMethod.DIRECT_HTTP,
                    "fallback_occurred": False,
                    "error": "Browser service not configured"
                })
                return EvidencePacket(
                    claim=claim,
                    source=url,
                    source_type=self.classify_source(url),
                    is_primary=False,
                    confidence=0.0,
                    extract="Error: Browser execution required but browser service is unavailable.",
                    conflicts=["Browser service unavailable"]
                )

            try:
                self.browser_service.navigate(url)
                packet = self.browser_service.extract_structured_evidence(claim)
                self.method_history.append({
                    "url": url,
                    "method": RetrievalMethod.PLAYWRIGHT_BROWSER,
                    "fallback_occurred": fallback_occurred,
                    "fallback_reason": fallback_reason,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                self.evidence_store.append(packet)
                return packet
            except Exception as e:
                # Browser failed; fallback or escalate to human
                self.method_history.append({
                    "url": url,
                    "method": RetrievalMethod.PLAYWRIGHT_BROWSER,
                    "fallback_occurred": True,
                    "error": str(e),
                    "escalation": RetrievalMethod.HUMAN_ESCALATION,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                return EvidencePacket(
                    claim=claim,
                    source=url,
                    source_type="playwright_browser",
                    is_primary=False,
                    confidence=0.0,
                    extract=f"Browser extraction failed: {str(e)}",
                    conflicts=[str(e)]
                )

        # 3. Process Direct HTTP result
        self.method_history.append({
            "url": url,
            "method": RetrievalMethod.DIRECT_HTTP,
            "fallback_occurred": False,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        observed_time = datetime.now(timezone.utc)
        source_type = self.classify_source(url)

        if fetch_result.get("error"):
            return EvidencePacket(
                claim=claim,
                source=url,
                source_type=source_type,
                observed_at=observed_time,
                is_primary=False,
                confidence=0.0,
                extract=f"Error retrieving source: {fetch_result['error']}",
                conflicts=[fetch_result["error"]],
                verifying_agent="Internet_Intelligence_Lead"
            )

        extracted = fetch_result.get("extracted_text", "")
        sanitized = self.sanitize_untrusted_content(extracted)

        packet = EvidencePacket(
            claim=claim,
            source=url,
            source_type=source_type,
            observed_at=observed_time,
            is_primary=True,
            confidence=0.95,
            extract=sanitized[:500],
            verifying_agent="Internet_Intelligence_Lead"
        )
        self.evidence_store.append(packet)
        return packet

