"""Real-browser check of the Agent Missions panel (Chromium via Playwright).

Logs in through the actual UI, plans, approves and runs a mission, and checks
the verified download link appears. Also fails on any console error, which
catches Content-Security-Policy violations. The model is scripted (SIMULATED).
"""
import os
from pathlib import Path

import pytest

# All tests here drive a real headless Chromium; skipped on Windows CI (Linux-served surface).
pytestmark = pytest.mark.browser_e2e

from services.browser.browser_service import _preinstalled_chromium
from tests.agents.test_agent_engine import OBJECTIVE, needs_netns
from tests.agents.test_agent_http import stack  # noqa: F401  (fixture)

playwright = pytest.importorskip("playwright.sync_api")


def _launch(p):
    exe = _preinstalled_chromium()
    try:
        return p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
    except Exception as exc:  # no browser on this host: report, do not pass
        pytest.skip(f"Chromium unavailable: {exc}")


@needs_netns
@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_agent_panel_end_to_end_in_browser(stack, viewport):  # noqa: F811
    base, _, engine, _ = stack
    errors = []
    with playwright.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport=viewport)
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        missing = []
        page.on("response", lambda r: missing.append(r.url) if r.status == 404 else None)
        page.goto(base + "/classic")
        page.fill("#login-username", "owner")
        page.fill("#login-password", "OwnerPassword123!")
        page.click("#login-form button[type=submit]")
        page.wait_for_selector("#auth-modal", state="hidden")
        page.locator("[data-target=tab-missions]:visible").first.click()
        page.fill("#agent-mission-objective", OBJECTIVE)
        page.click("#agent-mission-form button[type=submit]")
        page.wait_for_selector("#agent-mission-feedback:has-text('Plan ready')", timeout=30000)
        page.click("text=Approve this plan and budget")
        page.wait_for_selector("text=Run agents", timeout=15000)
        page.click("text=Run agents")
        page.wait_for_selector("text=Download verified result", timeout=120000)
        text = page.inner_text("#agent-mission-records")
        assert "State: COMPLETED" in text and "SIMULATED MODEL" in text
        assert "Independent check: PASS" in text
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        shots = os.environ.get("HOOD_SCREENSHOT_DIR")
        if shots:  # release evidence: python -m pytest ... with HOOD_SCREENSHOT_DIR=release/screenshots
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.locator("#agent-mission-records").scroll_into_view_if_needed()
            page.screenshot(path=str(Path(shots) / f"agent-missions-{viewport['width']}px.png"), full_page=False)
        browser.close()
    csp = [e for e in errors if "Content Security Policy" in e]
    assert not csp, csp
    # 401s before login are expected network errors from polling; anything else is a defect.
    unexpected = [e for e in errors if "401" not in e and "Content Security Policy" not in e]
    assert not unexpected, (unexpected, missing)
    assert overflow is False, "page scrolls horizontally at this viewport"
