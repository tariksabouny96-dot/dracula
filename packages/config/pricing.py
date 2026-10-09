"""Model price metadata used for pre-flight cost reservation.

Prices are operator-supplied (never guessed by Hood). Each entry records its
source and date so stale or missing prices are visible. A paid provider/model
without an entry is *unknown cost* and the router refuses to call it.

Configure with the ``HOOD_MODEL_PRICING`` environment variable pointing at a
JSON file shaped like::

    {"gemini": {"gemini-2.5-flash": {"input_per_1k_usd": 0.0003,
                                      "output_per_1k_usd": 0.0025,
                                      "as_of": "2026-10-01", "source": "provider price page"}}}
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


def load_price_table(path: Optional[str] = None) -> Dict[str, Dict[str, ModelPrice]]:
    path = path or os.environ.get("HOOD_MODEL_PRICING")
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    table: Dict[str, Dict[str, ModelPrice]] = {}
    for provider, models in raw.items():
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
