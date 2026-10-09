"""
HOOD Model Gateway - Gemini Provider Adapter
Governed by Master System Specification Section 8 & Build Instructions Section 9.
Upgraded in HOOD v0.1 for live authenticated Google Gemini API execution.
"""

import os
import json
import time
import urllib.request
import urllib.error
from typing import Optional, List
from packages.contracts import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderName,
    ModelClass
)
from packages.auth.vault import SecretVault
from .base import BaseModelProvider, ProviderNotConfiguredError, ProviderRateLimitError, ProviderError


class GeminiProviderAdapter(BaseModelProvider):
    def __init__(
        self,
        vault: Optional[SecretVault] = None,
        enabled: bool = True,
        api_key_secret_ref: str = "SECRET://gemini/api_key",
        timeout_sec: int = 30
    ):
        super().__init__(ProviderName.GEMINI, enabled=enabled)
        self.vault = vault or SecretVault()
        self.api_key_secret_ref = api_key_secret_ref
        self.timeout_sec = timeout_sec

    def _get_api_key(self) -> Optional[str]:
        # 1. Check vault reference
        key = self.vault.get_secret(self.api_key_secret_ref)
        if key:
            return key
        # 2. Check environment variable fallback
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def is_healthy(self) -> bool:
        if not self.enabled:
            return False
        return bool(self._get_api_key())

    def _select_candidate_models(self, model_class: ModelClass) -> List[str]:
        """Use explicit, operator-selected models. Do not silently change quality tiers.

        Defaults remain conservative and can be overridden without source edits.
        Model availability depends on the API account and can change over time.
        """
        env_name = {
            ModelClass.FAST: 'HOOD_GEMINI_FAST_MODEL',
            ModelClass.STANDARD: 'HOOD_GEMINI_STANDARD_MODEL',
            ModelClass.DEEP: 'HOOD_GEMINI_DEEP_MODEL',
        }.get(model_class, 'HOOD_GEMINI_STANDARD_MODEL')
        model = os.getenv(env_name, 'gemini-2.5-flash').strip()
        if not model or not all(c.isalnum() or c in '.-_' for c in model):
            raise ProviderNotConfiguredError('Invalid Gemini model configuration')
        return [model]

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise ProviderNotConfiguredError("Gemini provider is currently disabled.")

        api_key = self._get_api_key()
        if not api_key:
            raise ProviderNotConfiguredError(
                f"Gemini API key not found in vault ({self.api_key_secret_ref}) or environment. "
                "Configure key in vault to enable live Gemini API calls."
            )

        candidate_models = self._select_candidate_models(request.model_class)
        last_error = None

        for model_name in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            payload_dict = {
                "contents": [
                    {
                        "parts": [{"text": request.prompt}]
                    }
                ],
                "generationConfig": {
                    "temperature": request.temperature,
                    "maxOutputTokens": request.max_tokens
                }
            }
            if request.system_prompt:
                payload_dict["systemInstruction"] = {
                    "parts": [{"text": request.system_prompt}]
                }

            payload_bytes = json.dumps(payload_dict).encode("utf-8")
            http_req = urllib.request.Request(
                url,
                data=payload_bytes,
                headers={"Content-Type": "application/json", "User-Agent": "HOOD-Agent/0.1", "x-goog-api-key": api_key}
            )

            start_time = time.time()
            try:
                with urllib.request.urlopen(http_req, timeout=self.timeout_sec) as resp:
                    raw_data = resp.read(2_000_001)
                    if len(raw_data) > 2_000_000:
                        raise ProviderError("Gemini response exceeds allowed size")
                    raw_data = raw_data.decode("utf-8")
                    data = json.loads(raw_data)

                elapsed_ms = int((time.time() - start_time) * 1000)

                candidates = data.get("candidates", [])
                text_result = ""
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    text_result = "".join(p.get("text", "") for p in parts)

                usage_meta = data.get("usageMetadata", {})
                prompt_tokens = usage_meta.get("promptTokenCount", 0)
                candidates_tokens = usage_meta.get("candidatesTokenCount", 0)

                if not text_result.strip():
                    raise ProviderError("Gemini response contained no text")
                return ModelResponse(
                    text=text_result,
                    provider=self.provider_name,
                    model_name=model_name,
                    usage=ModelUsage(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=candidates_tokens,
                        total_tokens=prompt_tokens + candidates_tokens,
                        estimated_cost_usd=0.0  # UNKNOWN; this field is not a billing receipt
                    ),
                    latency_ms=elapsed_ms,
                    is_mock=False,
                    is_fallback=(model_name != candidate_models[0])
                )

            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                if e.code == 429:
                    last_error = ProviderRateLimitError(f"Gemini quota/rate limit: {err_body[:200]}")
                elif e.code in (503, 500):
                    last_error = ProviderError(f"Gemini server error ({e.code}): {err_body[:200]}")
                else:
                    last_error = ProviderError(f"Gemini API HTTP {e.code}: {err_body[:200]}")
                # Authorization, invalid requests and billing failures are not retried.
                if e.code not in (429, 500, 503):
                    raise last_error
                continue

            except ProviderError:
                raise
            except Exception as e:
                last_error = ProviderError(f"Gemini connection failed ({model_name}): {str(e)}")
                continue

        if last_error:
            raise last_error
        raise ProviderError("Gemini invocation failed across all candidate models.")
