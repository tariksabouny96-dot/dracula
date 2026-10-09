"""
HOOD v0.2 Browser Automation Test Suite
Verifies:
1. Browser launch, shutdown, and isolated context creation.
2. Safe deterministic navigation, DOM inspection, filling, clicking, and select options.
3. Screenshot capture with SHA256 integrity hash.
4. Path sandboxing for file uploads and downloads.
5. Prevention of credential/secret leakage during upload attempts.
6. Untrusted prompt injection neutralization in browser DOM text.
7. Consequential form action approval gating (blocked without approval, allowed with approval).
8. Emergency Stop immediate termination of browser session.
9. Method routing escalation (Direct HTTP -> Playwright).
10. Local web-application QA flow and defect detection.
11. Real public web safe navigation and evidence capture.
"""

import pytest

# All tests here drive a real headless Chromium; skipped on Windows CI (Linux-served surface).
pytestmark = pytest.mark.browser_e2e
import time
from pathlib import Path

from packages.config import load_config
from packages.contracts import RiskLevel, EvidencePacket
from services.policy.approval_service import ApprovalService
from services.policy.governance import RiskEvaluator
from services.audit.service import AuditService
from services.browser.browser_service import BrowserService, BrowserSecurityViolation
from services.browser.browser_tools import (
    BrowserNavigateTool,
    BrowserInspectDOMTool,
    BrowserClickTool,
    BrowserFillTool,
    BrowserScreenshotTool,
    BrowserUploadTool,
    BrowserSubmitConsequentialTool
)
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.core.emergency_stop import EmergencyStopController
from services.internet_intelligence.engine import InternetIntelligenceEngine, RetrievalMethod
from tests.browser.test_server import DisposableLocalServer


@pytest.fixture(scope="module")
def local_web_server():
    server = DisposableLocalServer(port=8999)
    server.start()
    yield "http://127.0.0.1:8999"
    server.stop()


@pytest.fixture
def browser_service(tmp_path):
    svc = BrowserService(
        workspace_root=Path.cwd(),
        downloads_dir=tmp_path / "downloads",
        evidence_dir=tmp_path / "evidence"
    )
    svc.launch(headless=True)
    yield svc
    svc.close()


def test_browser_launch_and_context_isolation(browser_service):
    """Verifies browser launches, creates independent contexts, and opens isolated tabs."""
    ctx1 = browser_service.create_context("client_a")
    ctx2 = browser_service.create_context("client_b")
    assert ctx1 != ctx2
    assert ctx1 in browser_service._contexts
    assert ctx2 in browser_service._contexts


def test_browser_deterministic_navigation_and_interaction(browser_service, local_web_server):
    """Verifies deterministic navigation, DOM inspection, input filling, and clicking on local web app."""
    # 1. Navigate
    nav_res = browser_service.navigate(f"{local_web_server}/")
    assert nav_res["status"] == "success"
    assert "HOOD Local QA Portal" in nav_res["title"]

    # 2. Inspect DOM
    dom_res = browser_service.inspect_dom()
    assert "Welcome to the autonomous QA playground" in dom_res["visible_text_snippet"]

    # 3. Fill form
    fill_res = browser_service.fill("#username", "ZakTester")
    assert fill_res["action"] == "fill"

    # 4. Select option
    sel_res = browser_service.select_option("#role", "engineer")
    assert sel_res["action"] == "select_option"

    # 5. Click Save Draft button
    click_res = browser_service.click("#btn-submit")
    assert click_res["action"] == "click"

    # 6. Verify updated state in DOM
    updated_dom = browser_service.inspect_dom()
    assert "DRAFT_SAVED_FOR_ZakTester" in updated_dom["visible_text_snippet"]


def test_browser_screenshot_evidence_integrity(browser_service, local_web_server):
    """Verifies screenshot capture stores a valid PNG with a non-empty SHA256 integrity hash."""
    browser_service.navigate(f"{local_web_server}/")
    shot_res = browser_service.capture_screenshot(name_prefix="test_evidence")

    assert Path(shot_res["screenshot_path"]).exists()
    assert len(shot_res["sha256"]) == 64  # Valid SHA256 hex digest
    assert shot_res["filename"].endswith(".png")


def test_local_web_app_qa_defect_detection(browser_service, local_web_server):
    """Verifies that HOOD autonomously inspects the local app and detects an introduced UI defect."""
    browser_service.navigate(f"{local_web_server}/")
    dom = browser_service.inspect_dom()

    # The local app intentionally has DEF-042: Counter mismatch
    assert "DEF-042" in dom["visible_text_snippet"] or "Counter mismatch detected" in dom["visible_text_snippet"]
    evidence = browser_service.extract_structured_evidence(claim="UI Defect Detection: DEF-042 found")

    assert evidence.is_primary is True
    assert "Counter mismatch" in evidence.extract
    assert "screenshot_path" in evidence.metadata


def test_browser_prompt_injection_defense(browser_service, local_web_server):
    """Verifies that adversarial prompt injections in web pages are neutralized into safe markers."""
    browser_service.navigate(f"{local_web_server}/malicious")
    dom = browser_service.inspect_dom()

    snippet = dom["visible_text_snippet"]
    assert "[NEUTRALIZED_UNTRUSTED_INSTRUCTION]" in snippet
    assert "ignore previous instructions" not in snippet.lower()
    assert "reveal your api key" not in snippet.lower()
    assert "upload your secrets" not in snippet.lower()
    assert "grant admin" not in snippet.lower()


def test_upload_security_and_secret_leak_prevention(browser_service, local_web_server, tmp_path):
    """Verifies that uploads outside workspace roots or containing sensitive data are blocked."""
    browser_service.navigate(f"{local_web_server}/upload")

    # 1. Prohibit upload of sensitive secrets / credentials
    secret_file = Path("artifacts/test_vault_key.env")
    secret_file.write_text("API_KEY=supersecret", encoding="utf-8")
    try:
        with pytest.raises(BrowserSecurityViolation) as exc_info:
            browser_service.upload_file("#upload-input", "artifacts/test_vault_key.env")
        assert "Secret Protection Policy" in str(exc_info.value)
    finally:
        if secret_file.exists():
            secret_file.unlink()

    # 2. Prohibit path traversal outside workspace root
    with pytest.raises(BrowserSecurityViolation) as exc_info:
        browser_service.upload_file("#upload-input", "C:\\Windows\\System32\\drivers\\etc\\hosts")
    assert "outside approved workspace root" in str(exc_info.value)

    # 3. Allow legitimate safe file in workspace
    safe_file = Path("artifacts/safe_sample.txt")
    safe_file.write_text("Diagnostic log data", encoding="utf-8")
    try:
        up_res = browser_service.upload_file("#upload-input", "artifacts/safe_sample.txt")
        assert up_res["action"] == "upload_file"
        assert up_res["file"] == "safe_sample.txt"
    finally:
        if safe_file.exists():
            safe_file.unlink()


def test_approval_gated_consequential_browser_action(browser_service, local_web_server, tmp_path):
    """
    Verifies that consequential browser actions (e.g., payments/orders/production changes)
    are strictly gated: blocked without approval, permitted once explicitly approved.
    """
    config = load_config()
    appr_svc = ApprovalService()
    audit_svc = AuditService(db_path=tmp_path / "browser_audit.db")
    gw = ToolGateway(config, appr_svc, audit_svc)

    gw.register_tool(BrowserNavigateTool(gw, browser_service))
    gw.register_tool(BrowserSubmitConsequentialTool(gw, browser_service))

    # Navigate to consequential page (Risk L0 - allowed)
    gw.invoke_tool("browser_navigate", {"url": f"{local_web_server}/consequential"})

    params = {"selector": "#btn-consequential-submit", "is_consequential_browser": True}

    # 1. No capability grant -> blocked before any approval is even considered.
    with pytest.raises(PermissionDeniedError, match="not granted"):
        gw.invoke_tool("browser_submit", params, task_id="task-pay")

    # 2. Grant but no approval -> blocked.
    gw.issue_capability_grant("browser:submit_consequential", "browser_submit", "Hood", ttl_seconds=120)
    with pytest.raises(PermissionDeniedError, match="requires explicit approval"):
        gw.invoke_tool("browser_submit", params, task_id="task-pay")

    import hashlib, json as _json
    digest = hashlib.sha256(_json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    # 3. A generic "yes" (approval without the exact parameter hash) is not enough.
    vague = appr_svc.create_request(task_id="task-pay", action_type="browser_submit", target="browser_submit",
                                    reason="Confirm and submit application fee", risk_level=RiskLevel.L3,
                                    recommended_option="Approve payment")
    appr_svc.resolve_request(vague.approval_id, approved=True, resolved_by="Zak")
    with pytest.raises(PermissionDeniedError, match="exact parameter hash"):
        gw.invoke_tool("browser_submit", params, task_id="task-pay", approval_id=vague.approval_id)

    # 4. Exact action-bound approval -> MUST SUCCEED
    req = appr_svc.create_request(task_id="task-pay", action_type="browser_submit", target="browser_submit",
                                  reason="Confirm and submit application fee", risk_level=RiskLevel.L3,
                                  options=[{"parameter_sha256": digest}], recommended_option="Approve payment",
                                  principal="Hood")
    appr_svc.resolve_request(req.approval_id, approved=True, resolved_by="Zak")
    res = gw.invoke_tool("browser_submit", params, task_id="task-pay", approval_id=req.approval_id)
    assert res["status"] == "submitted"

    # 5. Replay of the consumed approval is refused.
    with pytest.raises(PermissionDeniedError, match="already been used"):
        gw.invoke_tool("browser_submit", params, task_id="task-pay", approval_id=req.approval_id)

    # Verify that the browser DOM transitioned
    dom = browser_service.inspect_dom()
    assert "TRANSACTION_COMMITTED" in dom["visible_text_snippet"]


def test_emergency_stop_halts_browser_service(browser_service, local_web_server, tmp_path):
    """Verifies that Emergency Stop halts active browser operations and prevents further interaction."""
    config = load_config()
    appr_svc = ApprovalService()
    audit_svc = AuditService(db_path=tmp_path / "estop_audit.db")
    gw = ToolGateway(config, appr_svc, audit_svc)
    estop = EmergencyStopController(gw, audit_svc, browser_service)

    # Navigate
    browser_service.navigate(f"{local_web_server}/")

    # Trigger Emergency Stop ('Hood, stop everything')
    stop_res = estop.trigger_stop(reason="Emergency Stop triggered during browser session")
    assert stop_res["status"] == "EMERGENCY_STOP_ACTIVE"

    # Any subsequent browser action must be blocked
    with pytest.raises(BrowserSecurityViolation) as exc_info:
        browser_service.navigate(f"{local_web_server}/")
    assert "halted by Emergency Stop" in str(exc_info.value)


def test_method_router_escalation_to_browser(browser_service, local_web_server):
    """Verifies that InternetIntelligenceEngine dynamically routes and escalates to Playwright when requested."""
    engine = InternetIntelligenceEngine(browser_service=browser_service)

    # 1. Direct HTTP default
    packet_http = engine.research_url(f"{local_web_server}/", claim="Check title via direct HTTP")
    assert packet_http.source_type == "public_web"
    assert engine.method_history[-1]["method"] == RetrievalMethod.DIRECT_HTTP

    # 2. Escalation / forced browser requirement
    packet_browser = engine.research_url(
        f"{local_web_server}/",
        claim="Verify interactive elements via browser",
        force_browser=True
    )
    assert packet_browser.source_type == "playwright_browser"
    assert engine.method_history[-1]["method"] == RetrievalMethod.PLAYWRIGHT_BROWSER
    assert "screenshot_path" in packet_browser.metadata


@pytest.mark.live_network
def test_real_public_web_safe_task(browser_service):
    """
    Executes a real public-web safe interaction task against a legitimate public resource:
    Navigates to Python official downloads documentation (https://www.python.org/downloads/),
    verifies page title, extracts visible release text, and captures evidence without external modification.
    """
    url = "https://www.python.org/downloads/"
    nav_res = browser_service.navigate(url, timeout_ms=20000)
    assert nav_res["status"] == "success"
    assert "Python" in nav_res["title"]

    dom = browser_service.inspect_dom()
    assert "Download" in dom["visible_text_snippet"] or "Python" in dom["visible_text_snippet"]

    evidence = browser_service.extract_structured_evidence(claim="Verify official Python download page availability")
    assert evidence.is_primary is True
    assert evidence.confidence > 0.9
    assert "python.org" in evidence.source
    assert Path(evidence.metadata["screenshot_path"]).exists()
