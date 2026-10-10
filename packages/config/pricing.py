"""Model price metadata used for pre-flight cost reservation.

Prices are operator-supplied (never guessed by Hood). Each entry records its
source and date so stale or missing prices are visible. A paid provider/model
without an entry is *unknown cost* and the router refuses to call it.

Configure with the ``HOOD_MODEL_PRICING`` environment variable pointing at a
JSON file shaped like::

    {"gemini": {"gemini-3.8-flash": {"input_per_1k_usd": 0.0003,
                                      "output_per_1k_usd": 0.0025,
                                      "as_of": "2026-10-01", "source": "provider price page"}}}

(``config/model_pricing.free-tier.json`` is a ready file for a free-tier key.)
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

# Providers that never bill (loopback inference, deterministic test stubs).
NON_BILLING_PROVIDERS = {"local", "mock"}


@dataclass(frozen=True)
class ModelPrice:
    input_per_1k_usd: float
    output_per_1k_usd: float
    as_of: str
    source: str


def settings_pricing_file() -> Path:
    """Prices the owner saved in the UI (Settings > Model provider)."""
    return Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")).expanduser() / "model_pricing.json"


def load_price_table(path: Optional[str] = None) -> Dict[str, Dict[str, ModelPrice]]:
    """Explicit path, else the owner's Settings choice, else HOOD_MODEL_PRICING."""
    if not path and settings_pricing_file().is_file():
        path = str(settings_pricing_file())
    path = path or os.environ.get("HOOD_MODEL_PRICING")
    if not path:
        return {}
    file = Path(path).expanduser()
    if not file.is_absolute() and not file.exists():
        # Relative paths (e.g. config/model_pricing.free-tier.json in .env) also
        # resolve against the repo root, so Hood works when started elsewhere.
        file = Path(__file__).resolve().parents[2] / file
    raw = json.loads(file.read_text(encoding="utf-8"))
    table: Dict[str, Dict[str, ModelPrice]] = {}
    for provider, models in raw.items():
        if provider.startswith("_"):  # "_comment" and other annotations
            continue
        if not isinstance(models, dict):
            raise ValueError(f"Pricing for provider {provider!r} must be an object")
        for model, entry in models.items():
            price = ModelPrice(float(entry["input_per_1k_usd"]), float(entry["output_per_1k_usd"]),
                               str(entry.get("as_of", "unknown")), str(entry.get("source", "unspecified")))
            if price.input_per_1k_usd < 0 or price.output_per_1k_usd < 0:
                raise ValueError(f"Negative price for {provider}/{model}")
            table.setdefault(provider, {})[model] = price
    return table


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (~3 characters per token) for pre-flight reservations."""
    return max(1, len(text or "") // 3 + 1)
