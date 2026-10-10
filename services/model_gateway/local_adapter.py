"""Local OpenAI-compatible inference adapter with a loopback-only endpoint."""
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from packages.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderName
from .base import BaseModelProvider, ProviderError, ProviderNotConfiguredError


def validate_local_endpoint(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ProviderNotConfiguredError("Local inference requires an uncredentialed HTTP loopback URL")
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ProviderNotConfiguredError("Remote inference endpoints are not permitted by the local adapter")
    if not parsed.port or not parsed.path.endswith("/chat/completions"):
        raise ProviderNotConfiguredError("Invalid local inference endpoint")
    return url


class LocalProviderAdapter(BaseModelProvider):
    def __init__(self, endpoint_url="http://127.0.0.1:11434/v1/chat/completions",
                 model_name="llama3:8b", enabled=True, timeout_seconds=30.0):
        super().__init__(ProviderName.LOCAL, enabled=enabled)
        self.endpoint_url = validate_local_endpoint(endpoint_url)
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def is_healthy(self):
        return self.enabled  # Readiness not verified until invocation.

    def invoke(self, request: ModelRequest):
        if not self.enabled:
            raise ProviderNotConfiguredError("Local provider disabled")
        validate_local_endpoint(self.endpoint_url)
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})
        payload = {"model": self.model_name, "messages": messages,
                   "temperature": request.temperature, "max_tokens": request.max_tokens}
        req = urllib.request.Request(self.endpoint_url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        try:
            # Disable redirects: they can lead outside the local machine.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=self.timeout_seconds) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ProviderError("Local model response too large")
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise ProviderError("Local model response contains no text")
            usage = data.get("usage") or {}
            return ModelResponse(text=text, provider=ProviderName.LOCAL, model_name=self.model_name,
                usage=ModelUsage(prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0), estimated_cost_usd=0.0),
                latency_ms=int((time.monotonic()-started)*1000), is_mock=False)
        except ProviderError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise ProviderError("Local model endpoint unavailable") from exc
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ProviderError("Malformed local model response") from exc
