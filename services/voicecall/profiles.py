"""Speech-provider profiles: which model speaks, in which mode, and what it costs.

This is the only place that knows vendor model names and prices. Prices are *quotations* with a source
and date, never guesses; ``worst_case_usd_per_minute`` assumes the model both listens and speaks for the
whole minute, so reservations over-estimate rather than under-estimate.

Quoted prices (read 2026-10-10): Gemini Live from Google's pricing page; OpenAI Realtime from OpenAI's
model docs as reported by search (the pricing page itself returned 403 when fetched) so it is marked
secondary and disabled by default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

AUDIO_TOKENS_PER_SECOND = 25      # Google: "25 tokens per second of audio"


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    mode: str                                  # "realtime" | "cascade"
    vendor: str
    model: str
    voice: str
    audio_in_usd_per_m: float                  # USD per 1M audio tokens
    audio_out_usd_per_m: float
    text_in_usd_per_m: float = 0.0
    text_out_usd_per_m: float = 0.0
    languages: tuple = ("en", "fr")            # languages validated for use; others are "untested"
    enabled_by_default: bool = True
    source: str = ""
    as_of: str = ""
    quoted: bool = True                        # True = from the vendor's own page; False = secondary/estimate

    # ----------------------------------------------------------------- cost
    def usd(self, audio_in_s: float = 0.0, audio_out_s: float = 0.0,
            text_in_tokens: int = 0, text_out_tokens: int = 0) -> float:
        return (audio_in_s * AUDIO_TOKENS_PER_SECOND * self.audio_in_usd_per_m / 1e6
                + audio_out_s * AUDIO_TOKENS_PER_SECOND * self.audio_out_usd_per_m / 1e6
                + text_in_tokens * self.text_in_usd_per_m / 1e6
                + text_out_tokens * self.text_out_usd_per_m / 1e6)

    @property
    def worst_case_usd_per_minute(self) -> float:
        return self.usd(audio_in_s=60, audio_out_s=60)


PROFILES: Dict[str, ProviderProfile] = {p.id: p for p in (
    ProviderProfile(
        id="gemini-live", mode="realtime", vendor="google", model="gemini-3.8-live", voice="Puck",
        audio_in_usd_per_m=3.00, audio_out_usd_per_m=12.00, text_in_usd_per_m=0.75, text_out_usd_per_m=4.50,
        source="https://ai.google.dev/gemini-api/docs/pricing (Gemini 3.8 Live; free tier exists)", as_of="2026-10-10"),
    ProviderProfile(
        # STT and TTS go through Hood's existing consent/cost-tracked Gemini voice; the per-minute figure
        # here is only the worst-case reservation, actual spend is recorded by Hood's CostController.
        id="gemini-cascade", mode="cascade", vendor="google", model="gemini-3.5-flash-lite+gemini-3.8-flash-tts",
        voice="Puck", audio_in_usd_per_m=0.30, audio_out_usd_per_m=9.00,
        source="https://ai.google.dev/gemini-api/docs/pricing (3.5-flash-lite input; 3.8-flash-tts audio output)",
        as_of="2026-10-10"),
    ProviderProfile(
        id="openai-realtime", mode="realtime", vendor="openai", model="gpt-realtime", voice="alloy",
        audio_in_usd_per_m=32.00, audio_out_usd_per_m=64.00, text_in_usd_per_m=4.00, text_out_usd_per_m=16.00,
        enabled_by_default=False, quoted=False,
        source="OpenAI model docs via search (pricing page returned HTTP 403); needs HOOD_ALLOW_OPENAI_API_CALLS=1",
        as_of="2026-10-10"),
)}


def get_profile(profile_id: str) -> ProviderProfile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise KeyError(f"Unknown voice provider profile: {profile_id}") from None


def minutes_cost_table(profile: ProviderProfile, minutes: List[int], *, speaking_share: float = 0.5,
                       livekit_usd_per_min: float = 0.0) -> List[Dict[str, float]]:
    """Estimated monthly cost for N conversation minutes (owner speaks ``1-speaking_share`` of the time)."""
    rows = []
    for m in minutes:
        per_min = profile.usd(audio_in_s=60, audio_out_s=60 * speaking_share) + livekit_usd_per_min
        rows.append({"minutes": m, "usd_estimate": round(per_min * m, 2), "usd_worst_case":
                     round((profile.worst_case_usd_per_minute + livekit_usd_per_min) * m, 2)})
    return rows
