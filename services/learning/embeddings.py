"""Pluggable embeddings for semantic lesson recall.

Embeddings are OPTIONAL. With a provider configured, recall ranks lessons by
cosine similarity of meaning. With none, learning honestly falls back to lexical
ranking and says so — a lexical match is never dressed up as semantic.

The real provider is Gemini embeddings, reached only through the egress firewall
(default deny) with the owner's credential. It is therefore owner-governed and,
like every live call, blocked unless the owner has allowed the host.
"""
from __future__ import annotations

import json
import math
import os
import urllib.request
from typing import List, Optional, Protocol


class EmbeddingProvider(Protocol):
    name: str

    def embed(self, texts: List[str]) -> List[List[float]]:
        ...


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class GeminiEmbeddingProvider:
    """Real embeddings via the Gemini embedContent API, firewall-gated.

    Credentials come from the chat adapter (``key_source``: the key saved in Settings, then
    GEMINI_API_KEY/GOOGLE_API_KEY, then an injecting proxy), so a key saved in Settings works here
    too (before security batch 1 embeddings only read the environment). Every call is authorised
    by the firewall first (default deny) and, with a ``router``, needs a price on file and is
    reserved and charged against the spend caps like every other paid call (embedContent reports
    no token usage, so the conservative estimate is charged).
    """

    name = "gemini"
    HOST = "generativelanguage.googleapis.com"

    def __init__(self, firewall=None, model: Optional[str] = None, timeout_sec: int = 30,
                 key_source=None, proxy_source=None, router=None):
        self.firewall = firewall
        self.key_source = key_source
        self.proxy_source = proxy_source
        self.router = router
        # Operator-overridable; the served embedding model drifts by API version
        # (gemini-embedding-001 is current; text-embedding-00x is retired here).
        self.model = model or os.environ.get("HOOD_GEMINI_EMBED_MODEL", "gemini-embedding-001")
        self.timeout_sec = timeout_sec

    def _api_key(self) -> Optional[str]:
        if self.key_source is not None:
            return self.key_source()
        return (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip() or None

    def _proxy(self) -> bool:
        if self.proxy_source is not None:
            return bool(self.proxy_source())
        return os.environ.get("HOOD_GEMINI_CREDENTIAL", "").strip().lower() == "proxy" and \
            any(os.environ.get(v) for v in ("HTTPS_PROXY", "https_proxy"))

    def _price(self):
        if self.router is None:
            return None
        price = (getattr(self.router, "price_table", {}) or {}).get("gemini", {}).get(self.model)
        if price is None:
            raise RuntimeError(f"No price on file for gemini/{self.model}; HOOD refuses unknown-cost calls "
                               "(add it in Settings > Model provider or the pricing file)")
        return price

    def available(self) -> bool:
        return bool(self._api_key()) or self._proxy()

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self.firewall is not None:
            # Default-deny egress: raises FirewallBlocked if the host isn't allowed.
            self.firewall.enforce(self.HOST, 443, purpose="embeddings:gemini")
        api_key = self._api_key()
        if not api_key and not self._proxy():
            raise RuntimeError("No Gemini credential for embeddings")
        price = self._price()
        url = f"https://{self.HOST}/v1beta/models/{self.model}:embedContent"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-goog-api-key"] = api_key
        out: List[List[float]] = []
        for text in texts:
            payload = {"model": f"models/{self.model}",
                       "content": {"parts": [{"text": text[:8000]}]}}
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
            reservation, tokens, estimate = None, 0, 0.0
            if price is not None:
                from packages.config.pricing import estimate_tokens
                tokens = estimate_tokens(text[:8000])
                estimate = tokens / 1000.0 * price.input_per_1k_usd
                reservation = self.router.cost_controller.reserve(None, estimate)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    data = json.loads(resp.read(5_000_000))
            finally:
                if reservation is not None:
                    # May have been processed: charge the conservative estimate, never zero.
                    from packages.contracts import ModelUsage
                    self.router.cost_controller.settle(
                        reservation, ModelUsage(prompt_tokens=tokens, total_tokens=tokens), cost_measured=False,
                        provider="gemini", model=self.model,
                        price_source=f"{price.source} ({price.as_of}); estimated (no usage reported)")
            vec = (data.get("embedding") or {}).get("values")
            if not isinstance(vec, list) or not vec:
                raise RuntimeError("Gemini returned no embedding")
            out.append([float(x) for x in vec])
        return out
