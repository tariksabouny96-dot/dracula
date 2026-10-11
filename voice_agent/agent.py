"""The Hood voice agent.

Realtime mode: Gemini Live is the ears and mouth. It has exactly one tool, ``ask_hood``, and is told to call
it for everything the owner says and to speak Hood's answer verbatim. If the model speaks something that did
not come from Hood, the worker counts an "unbridged" turn and reports it; the server ends the call after
repeated violations (Hood's own checks do not depend on the model behaving).

Cascade mode: speech-to-text -> Hood -> text-to-speech. The model has no say at all.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
from typing import Any, Callable, List, Optional

from .hood_client import HoodClient, HoodError
from .usage import UsageMeter

log = logging.getLogger("hood.voice_agent")

REALTIME_INSTRUCTIONS = (
    "You are the voice of Hood, the owner's personal AI system, on a live phone call. You do not think for Hood "
    "and you never decide anything yourself. For EVERYTHING the owner says, including greetings, call the tool "
    "ask_hood with their words as heard, then say the text it returns aloud, word for word, adding and omitting "
    "nothing. If the tool returns nothing, stay silent. You cannot approve, authorise, confirm or run anything: "
    "never claim you did. Speak the owner's language ({language}). Be brief and natural."
)

_FILLER_MAX_WORDS = 5


def _norm(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def is_bridged(spoken: str, hood_replies: List[str]) -> bool:
    """True if what the model said matches something Hood told it to say (or is a short filler)."""
    s = _norm(spoken)
    if not s or len(s.split()) <= _FILLER_MAX_WORDS:
        return True
    for h in hood_replies:
        hn = _norm(h)
        if not hn:
            continue
        if s in hn or hn in s or difflib.SequenceMatcher(None, s, hn).ratio() >= 0.6:
            return True
    return False


class HoodBridge:
    """Everything the worker does with Hood, free of LiveKit types so it can be tested directly."""

    def __init__(self, hood: HoodClient, meter: UsageMeter):
        self.hood, self.meter = hood, meter
        self.replies: List[str] = []
        self.pending_end = False
        self.confirmations: List[dict] = []

    async def ask(self, utterance: str) -> str:
        try:
            r = await asyncio.to_thread(self.hood.turn, utterance)
        except HoodError as exc:
            log.warning("Hood turn failed: %s", exc)
            return "Sorry, I couldn't reach Hood just now. Please try again."
        if r.speech:
            self.replies.append(r.speech)
            del self.replies[:-8]
        self.pending_end = self.pending_end or r.end_call
        self.confirmations.extend(r.needs_confirmation)
        return r.speech

    def check_spoken(self, spoken: str) -> bool:
        ok = is_bridged(spoken, self.replies)
        if not ok:
            self.meter.unbridged()
        return ok


def build_agent(bridge: HoodBridge, info: dict, mode: str):
    """Construct the LiveKit Agent for the chosen mode (imports LiveKit lazily)."""
    from livekit.agents import Agent, RunContext, function_tool
    from livekit.agents.llm import StopResponse

    if mode == "realtime":
        class RealtimeHoodAgent(Agent):
            def __init__(self):
                super().__init__(instructions=REALTIME_INSTRUCTIONS.format(language=info.get("language", "en")))

            @function_tool
            async def ask_hood(self, context: RunContext, utterance: str) -> str:
                """Give Hood what the owner just said and receive exactly what to say back."""
                speech = await bridge.ask(utterance)
                if not speech:
                    return "Stay silent."
                return f'Say exactly this, word for word, and nothing else: "{speech}"'
        return RealtimeHoodAgent()

    class CascadeHoodAgent(Agent):
        def __init__(self):
            super().__init__(instructions="You relay Hood's answers.")

        async def on_user_turn_completed(self, turn_ctx, new_message):
            text = (new_message.text_content or "").strip()
            if text:
                speech = await bridge.ask(text)
                if speech:
                    await self.session.say(speech, allow_interruptions=True)
            raise StopResponse()
    return CascadeHoodAgent()
