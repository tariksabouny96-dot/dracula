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
        timeout_sec: int = 180
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

    @staticmethod
    def _proxy_credential() -> bool:
        """The key is injected by an egress proxy (e.g. a cloud "network secret").

        Hood never sees the key in this mode; it sends the request without one and
        the proxy adds ``x-goog-api-key`` for generativelanguage.googleapis.com only.
        """
        return os.environ.get("HOOD_GEMINI_CREDENTIAL", "").strip().lower() == "proxy"

    def is_healthy(self) -> bool:
        # Configuration presence, not a live probe (see probe()).
        if not self.enabled:
            return False
        return bool(self._get_api_key()) or self._proxy_credential()

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
        # gemini-2.5-* is closed to new API users (HTTP 404 on 2026-10-09); defaults follow the live list.
        default = {ModelClass.FAST: 'gemini-3.5-flash-lite', ModelClass.DEEP: 'gemini-3.8-flash'}.get(
            model_class, 'gemini-3.8-flash')
        model = (os.getenv(env_name) or "").strip() or default  # blank setting -> default
        if not model or not all(c.isalnum() or c in '.-_' for c in model):
            raise ProviderNotConfiguredError('Invalid Gemini model configuration')
        return [model]

    def resolve_model(self, request: ModelRequest) -> str:
        return self._select_candidate_models(request.model_class)[0]

    def candidate_models(self, request: ModelRequest) -> list:
        """Primary model, then operator-listed fallbacks used only when the primary is
        overloaded or rate-limited. Every candidate must be priced (checked by the router)."""
        primary = self._select_candidate_models(request.model_class)[0]
        raw = os.getenv("HOOD_GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite")
        out = [primary]
        for name in (m.strip() for m in raw.split(",")):
            if name and name != primary and all(c.isalnum() or c in ".-_" for c in name):
                out.append(name)
        return out

    MAX_RETRIES = 5

    @staticmethod
    def _retry_delay(err_body: str, attempt: int) -> float:
        """Honour Google's RetryInfo when present, else exponential backoff (capped)."""
        import re as _re
        match = _re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', err_body or "")
        delay = float(match.group(1)) if match else 2.0 * (2 ** attempt)
        return min(delay, 60.0)

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if not self.enabled:
            raise ProviderNotConfiguredError("Gemini provider is currently disabled.")

        api_key = self._get_api_key()
        if not api_key and not self._proxy_credential():
            raise ProviderNotConfiguredError(
                "Gemini API key not found: save it in Settings > Model provider (takes effect "
                "immediately), or add GEMINI_API_KEY=<your key> to the .env file in the HOOD folder "
                "and restart HOOD. "
                f"(Also checked: vault {self.api_key_secret_ref}, HOOD_GEMINI_CREDENTIAL=proxy.)"
            )

        models = self.candidate_models(request)
        generation = {"temperature": request.temperature, "maxOutputTokens": request.max_tokens}
        if request.response_mime_type:
            generation["responseMimeType"] = request.response_mime_type
        if request.response_schema:
            generation["responseMimeType"] = "application/json"
            generation["responseJsonSchema"] = request.response_schema
        payload_dict = {"contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
                        "generationConfig": generation}
        if request.system_prompt:
            payload_dict["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "HOOD-Agent/0.2"}
        if api_key:
            headers["x-goog-api-key"] = api_key

        last_error = None
        for model_index, model_name in enumerate(models):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            # Fewer retries when another model is listed: an overloaded model rarely recovers in seconds.
            has_fallback = model_index < len(models) - 1
            retries = 2 if has_fallback else self.MAX_RETRIES
            data = self._post_with_retries(url, payload_bytes, headers, model_name, retries,
                                           fail_fast_on_quota=has_fallback)
            if isinstance(data, Exception):
                last_error = data
                continue  # overloaded / rate-limited: try the next listed model
            return self._parse(data, request, model_name, start_time=data.pop("_hood_started"),
                               is_fallback=model_index > 0)
        raise last_error or ProviderError("Gemini invocation failed")

    def _post_with_retries(self, url, payload_bytes, headers, model_name, retries, fail_fast_on_quota=False):
        """Return parsed JSON, or a retryable error once retries are exhausted.

        A quota error (429) is not waited out when another model can take the request, or when
        the exhausted quota is a daily one: waiting cannot help, and the owner sees a stalled reply.
        """
        last_error = None
        for attempt in range(retries + 1):
            http_req = urllib.request.Request(url, data=payload_bytes, headers=headers)
            start_time = time.time()
            try:
                with urllib.request.urlopen(http_req, timeout=self.timeout_sec) as resp:
                    raw_data = resp.read(4_000_001)
                if len(raw_data) > 4_000_000:
                    raise ProviderError("Gemini response exceeds allowed size")
                data = json.loads(raw_data.decode("utf-8"))
                data["_hood_started"] = start_time
                return data
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                if e.code == 429:
                    last_error = ProviderRateLimitError(f"Gemini quota/rate limit ({model_name}): {err_body[:300]}")
                    if fail_fast_on_quota or "PerDay" in err_body:
                        return last_error
                elif e.code in (500, 502, 503, 504):
                    last_error = ProviderError(f"Gemini server error ({e.code}, {model_name}): {err_body[:300]}")
                elif e.code in (401, 403):
                    raise ProviderNotConfiguredError(f"Gemini rejected the credential (HTTP {e.code})") from None
                else:
                    # Invalid request / retired model: not retried, never replaced with other output.
                    raise ProviderError(f"Gemini API HTTP {e.code} ({model_name}): {err_body[:300]}") from None
                if attempt < retries:
                    time.sleep(self._retry_delay(err_body, attempt))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = ProviderError(f"Gemini connection failed ({model_name}): {e}")
                if attempt < retries:
                    time.sleep(self._retry_delay("", attempt))
        return last_error

    def _parse(self, data, request, model_name, *, start_time, is_fallback):
        elapsed_ms = int((time.time() - start_time) * 1000)
        candidates = data.get("candidates", [])
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise ProviderError(f"Gemini returned no answer ({reason})")
        candidate = candidates[0]
        finish = candidate.get("finishReason", "STOP")
        parts = candidate.get("content", {}).get("parts", [])
        text_result = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        truncated = finish == "MAX_TOKENS"
        if truncated and not (getattr(request, "allow_partial", False) and text_result.strip()):
            # A truncated answer is not an answer; never pass it on as complete.
            raise ProviderError(f"Gemini output truncated at maxOutputTokens={request.max_tokens}")
        if not truncated and finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
            raise ProviderError(f"Gemini stopped with finishReason={finish}")
        if not text_result.strip():
            raise ProviderError("Gemini response contained no text")
        usage_meta = data.get("usageMetadata", {})
        prompt_tokens = int(usage_meta.get("promptTokenCount", 0) or 0)
        # Thinking tokens are billed as output tokens.
        completion_tokens = int(usage_meta.get("candidatesTokenCount", 0) or 0) + \
            int(usage_meta.get("thoughtsTokenCount", 0) or 0)
        return ModelResponse(
            text=text_result,
            provider=self.provider_name,
            model_name=data.get("modelVersion") or model_name,
            usage=ModelUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                             total_tokens=prompt_tokens + completion_tokens,
                             estimated_cost_usd=0.0),  # priced by the router, not here
            latency_ms=elapsed_ms,
            is_mock=False,
            is_fallback=is_fallback,
            truncated=truncated,
        )
