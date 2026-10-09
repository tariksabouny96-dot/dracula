"""Explicitly enabled OpenAI Responses API transport for HOOD.

Only user-approved configuration activates this network adapter. Provider output is
untrusted; this adapter performs no tool calls and does not imply free API usage.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Optional

from packages.auth.vault import SecretVault
from packages.contracts import ModelClass, ModelRequest, ModelResponse, ModelUsage, ProviderName
from .base import BaseModelProvider, ProviderError, ProviderNotConfiguredError, ProviderRateLimitError

_MODEL_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$")


class OpenAIProviderAdapter(BaseModelProvider):
    def __init__(self, vault: Optional[SecretVault] = None, enabled: bool = False,
                 api_key_secret_ref: str = "SECRET://openai/api_key", timeout_sec: int = 30):
        super().__init__(ProviderName.OPENAI, enabled=enabled)
        self.vault = vault or SecretVault()
        self.api_key_secret_ref = api_key_secret_ref
        self.timeout_sec = timeout_sec

    def _api_key(self):
        return self.vault.get_secret(self.api_key_secret_ref) or os.getenv("OPENAI_API_KEY")

    def is_healthy(self) -> bool:
        # Configuration presence, not a live connectivity check.
        return bool(self.enabled and self._api_key())

    def _model(self, model_class):
        tier = {ModelClass.FAST: "FAST", ModelClass.STANDARD: "STANDARD", ModelClass.DEEP: "DEEP"}.get(model_class, "STANDARD")
        value = os.getenv(f"HOOD_OPENAI_{tier}_MODEL", "gpt-5-mini").strip()
        if not _MODEL_RE.fullmatch(value):
            raise ProviderNotConfiguredError("Invalid OpenAI model configuration")
        return value

    def resolve_model(self, request: ModelRequest) -> str:
        return self._model(request.model_class)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise ProviderNotConfiguredError("OpenAI provider is currently DISABLED; explicit opt-in required")
        if os.getenv("HOOD_ALLOW_OPENAI_API_CALLS") != "1":
            raise ProviderNotConfiguredError("OpenAI network calls require HOOD_ALLOW_OPENAI_API_CALLS=1")
        key = self._api_key()
        if not key:
            raise ProviderNotConfiguredError("OpenAI API key is not configured")
        if not (1 <= request.max_tokens <= 8192):
            raise ProviderError("Output token limit must be between 1 and 8192")
        model = self._model(request.model_class)
        payload = {"model": model, "input": request.prompt, "max_output_tokens": request.max_tokens,
                   "store": False}
        if request.system_prompt:
            payload["instructions"] = request.system_prompt
        http_request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_sec) as response:
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ProviderError("OpenAI response exceeds maximum size")
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("status") not in (None, "completed"):
                raise ProviderError("OpenAI did not report a completed response")
            pieces = []
            for item in data.get("output", []):
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "output_text":
                        pieces.append(content.get("text", ""))
            text = "".join(pieces)
            if not text.strip():
                raise ProviderError("OpenAI returned no text content")
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            return ModelResponse(text=text, provider=ProviderName.OPENAI, model_name=model,
                usage=ModelUsage(prompt_tokens=prompt_tokens, completion_tokens=output_tokens,
                                 total_tokens=prompt_tokens + output_tokens, estimated_cost_usd=0.0),
                latency_ms=int((time.monotonic() - start) * 1000), is_mock=False)
        except urllib.error.HTTPError as exc:
            # Do not echo API response bodies: upstream errors may contain sensitive data.
            if exc.code == 429:
                raise ProviderRateLimitError("OpenAI rate limit or quota reached") from exc
            raise ProviderError(f"OpenAI HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise ProviderError("OpenAI network connection failed") from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise ProviderError("Malformed OpenAI response") from exc
