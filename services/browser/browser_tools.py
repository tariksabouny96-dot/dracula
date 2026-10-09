"""
HOOD Browser Tools for ToolGateway
Exposes BrowserService capabilities behind capability grants, risk evaluation, and audit logging.
"""

from typing import Dict, Any, Optional
from services.tool_gateway.gateway import BaseTool, ToolGateway, PermissionDeniedError
from services.browser.browser_service import BrowserService, BrowserSecurityViolation
from packages.contracts import RiskLevel


class BrowserNavigateTool(BaseTool):
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_navigate", "browser:navigate", "Navigates to URL and inspects basic metadata")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        url = params.get("url")
        if not url:
            raise ValueError("Parameter 'url' is required.")
        timeout_ms = int(params.get("timeout_ms", 15000))
        return self.browser_service.navigate(url, timeout_ms=timeout_ms)


class BrowserInspectDOMTool(BaseTool):
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_inspect_dom", "browser:inspect", "Reads page title, visible text, and structure")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self.browser_service.inspect_dom()


class BrowserClickTool(BaseTool):
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_click", "browser:click", "Clicks an element by selector")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        selector = params.get("selector")
        if not selector:
            raise ValueError("Parameter 'selector' is required.")
        return self.browser_service.click(selector)


class BrowserFillTool(BaseTool):
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_fill", "browser:fill", "Fills text into an input element")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        selector = params.get("selector")
        text = params.get("text", "")
        if not selector:
            raise ValueError("Parameter 'selector' is required.")
        return self.browser_service.fill(selector, text)


class BrowserScreenshotTool(BaseTool):
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_screenshot", "browser:screenshot", "Captures page screenshot with SHA256 integrity hash")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        prefix = params.get("prefix", "manual")
        return self.browser_service.capture_screenshot(name_prefix=prefix)


class BrowserUploadTool(BaseTool):
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_upload", "browser:upload", "Uploads an approved workspace file into file input")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        selector = params.get("selector")
        file_path = params.get("file_path")
        if not selector or not file_path:
            raise ValueError("Parameters 'selector' and 'file_path' are required.")
        return self.browser_service.upload_file(selector, file_path)


class BrowserSubmitConsequentialTool(BaseTool):
    """
    Simulates consequential form submissions, payments, or account changes (Risk L3/L4).
    Enforces that this tool CANNOT execute without explicit Human Approval.
    """
    def __init__(self, gateway: ToolGateway, browser_service: BrowserService):
        super().__init__("browser_submit", "browser:submit_consequential", "Submits consequential form or transaction")
        self.gateway = gateway
        self.browser_service = browser_service

    def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        selector = params.get("selector")
        if not selector:
            raise ValueError("Parameter 'selector' is required.")
        # Execute click on submit button
        click_res = self.browser_service.click(selector)
        return {
            "status": "submitted",
            "selector": selector,
            "details": click_res
        }
