"""HOOD NEXT console in real Chromium against the real server (agent model scripted).

Checks: login, every page renders without console/CSP errors, no horizontal overflow at
1440/390 px, the mission flow (plan -> approve -> run -> verified download) works from the
new UI, server text is never interpreted as HTML, and widgets keep their layout per user.
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
PAGES = ["Command", "Missions", "Agents", "Intelligence", "Integrations", "Sentinel", "Memory", "Commerce",
         "Desktop", "Voice", "Settings"]


def _launch(p):
    exe = _preinstalled_chromium()
    try:
        return p.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
    except Exception as exc:
        pytest.skip(f"Chromium unavailable: {exc}")


def _login(page, base):
    page.goto(base + "/")
    page.fill("#authUser", "owner")
    page.fill("#authPass", "OwnerPassword123!")
    page.click("#authSubmit")
    page.wait_for_selector("#authOverlay", state="hidden")


def _errors(page):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors


@pytest.mark.parametrize("viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}])
def test_every_page_renders_cleanly(stack, viewport):  # noqa: F811
    base, _, engine, _ = stack
    # A mission whose objective contains markup: it must be shown as text, never parsed.
    engine.create_mission("user_root_owner_01", OBJECTIVE + " <img src=x onerror=window.__pwned=1>")
    shots = os.environ.get("HOOD_SCREENSHOT_DIR")
    with playwright.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport=viewport)
        errors = _errors(page)
        _login(page, base)
        page.wait_for_selector("[data-widget=hero]")
        for name in PAGES:
            if viewport["width"] < 800:
                page.click("#menuToggle")
            page.click(f"#nav [data-page={name}]")
            label = {"Intelligence": "System map"}.get(name, name)   # nav label differs from the page key
            page.wait_for_selector(f"#crumb:has-text('{label.upper()}')", state="attached")
            page.wait_for_timeout(400)
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            assert not overflow, f"{name} scrolls horizontally at {viewport['width']}px"
            if shots:
                Path(shots).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(shots) / f"next-{name.lower()}-{viewport['width']}.png"), full_page=False)
        assert page.evaluate("window.__pwned === undefined"), "server text was executed as HTML"
        browser.close()
    real = [e for e in errors if all(s not in e for s in ("401", "403", "404", "429", "503"))]
    assert not real, real


@needs_netns
def test_mission_flow_from_new_console(stack):  # noqa: F811
    base, _, engine, _ = stack
    with playwright.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = _errors(page)
        _login(page, base)
        page.wait_for_selector("#commandInput")
        page.fill("#commandInput", "/mission " + OBJECTIVE)
        page.press("#commandInput", "Enter")
        page.click("text=Plan mission")
        # One approval starts the agents (owner's request: no second "Run agents" click).
        page.wait_for_selector("button:has-text('Approve plan & start agents')", timeout=30000)
        page.click("button:has-text('Approve plan & start agents')")
        page.wait_for_selector("text=Download verified result", timeout=120000)
        detail = page.inner_text(".mission-detail")
        assert "COMPLETED" in detail and "simulated" in detail.lower()
        with page.expect_download() as dl:
            page.click("text=Download verified result")
        assert dl.value.suggested_filename.endswith(".zip")
        # Live feed delivered mission events to the Command page.
        page.click("#nav [data-page=Command]")
        page.wait_for_selector(".feed div b")
        browser.close()
    real = [e for e in errors if all(s not in e for s in ("401", "403", "404", "429", "503"))]
    assert not real, real


def test_layout_persists_per_user(stack):  # noqa: F811
    base, _, _, _ = stack
    with playwright.sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _login(page, base)
        page.wait_for_selector("[data-widget=cost]")
        page.click("[data-widget=cost] [aria-label='Hide Resources & cost']")
        page.wait_for_timeout(1200)  # debounced save
        page.reload()
        page.wait_for_selector("[data-widget=hero]")
        page.wait_for_timeout(500)
        assert page.locator("[data-widget=cost]").count() == 0
        page.click("text=Restore widgets")
        page.wait_for_selector("[data-widget=cost]")
        browser.close()


def test_sandbox_is_one_approval_then_hood_does_the_rest(stack, tmp_path):  # noqa: F811
    """Settings › Agents on a (simulated) Windows PC: the owner approves once, sees what HOOD is doing,
    and is offered the restart Windows asks for; no command to type."""
    from ui import routes
    from services.toolbox.wsl import WslSandbox
    from tests.toolbox.test_wsl_sandbox import WSL_EXE, FakeWindows, fetcher
    base, _, _, _ = stack
    win = FakeWindows(wsl_installed=False, needs_restart=True)
    sbx = WslSandbox(tmp_path / "wsl-data", runner=win, fetcher=fetcher())
    sbx.wsl_exe = lambda: WSL_EXE
    routes.SERVICES["wsl_sandbox"] = sbx
    shots = os.environ.get("HOOD_SCREENSHOT_DIR")
    try:
        with playwright.sync_playwright() as p:
            browser = _launch(p)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = _errors(page)
            _login(page, base)
            page.click("#nav [data-page=Settings]")
            panel = page.locator(".sandbox-panel")
            panel.locator("button:has-text('Allow & set up')").wait_for()
            assert "administrator prompt" in panel.inner_text()           # highlighted before approving
            if shots:
                panel.screenshot(path=os.path.join(shots, "sandbox-approve.png"))
            panel.locator("button:has-text('Allow & set up')").click()
            page.locator("#modalBody button:has-text('Allow & set up')").click()
            panel.locator("button:has-text('Restart now')").wait_for(timeout=20000)
            assert "Windows needs a restart" in panel.inner_text()
            assert "WSL isn't installed" in panel.text_content()          # what HOOD did, in its log
            assert "null" not in panel.inner_text()
            if shots:
                panel.screenshot(path=os.path.join(shots, "sandbox-restart.png"))
            browser.close()
        real = [e for e in errors if all(s not in e for s in ("401", "403", "404", "429", "503"))]
        assert not real, real
        assert sbx.approved() and sum(c[0] == "powershell.exe" for c in win.calls) == 1
    finally:
        routes.SERVICES.pop("wsl_sandbox", None)
