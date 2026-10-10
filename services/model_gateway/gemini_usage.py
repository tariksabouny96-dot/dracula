"""Gemini usageMetadata -> HOOD usage, the same way for chat, missions, voice and self-repair.

Billing facts used here (Gemini API pricing page): "thinking" tokens (``thoughtsTokenCount``) are
billed as output; audio input is billed at its own rate (``promptTokensDetails`` modality AUDIO).
Before security batch 1 voice left thinking tokens out and priced audio at the text rate.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from packages.contracts import ModelUsage


def usage_from_metadata(meta: Dict[str, Any]) -> Tuple[ModelUsage, int]:
    """(usage, audio prompt tokens). Completion includes thinking tokens; total = prompt + completion."""
    meta = meta or {}
    prompt = int(meta.get("promptTokenCount", 0) or 0)
    completion = int(meta.get("candidatesTokenCount", 0) or 0) + int(meta.get("thoughtsTokenCount", 0) or 0)
    audio = sum(int(d.get("tokenCount", 0) or 0) for d in (meta.get("promptTokensDetails") or [])
                if str(d.get("modality", "")).upper() == "AUDIO")
    return ModelUsage(prompt_tokens=prompt, completion_tokens=completion,
                      total_tokens=prompt + completion, estimated_cost_usd=0.0), audio


def price_note(price, audio_tokens: int) -> str:
    """Where the price came from, and whether audio had to be priced at the text rate."""
    note = f"{price.source} ({price.as_of})"
    if audio_tokens and price.audio_input_per_1k_usd is None and price.input_per_1k_usd > 0:
        note += "; audio priced at the text input rate (no audio price on file)"
    return note
