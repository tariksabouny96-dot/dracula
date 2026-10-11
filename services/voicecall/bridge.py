"""HoodVoiceBridge: connects a live call to the one Hood.

Every owner utterance becomes a text turn in Hood's normal orchestrator
(``InteractionService.handle_text_input(..., channel="voice")``), in the same per-owner conversation as
text chat, so memory, grounding, missions and policies are the ones Hood already has. The bridge adds:
speech-safe output, per-call turn limits, hang-up detection, cost attribution for the reasoning model,
and the voice rule: **speech never grants authority**; sensitive decisions are surfaced to the owner's
device for confirmation (confirm.py).
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .config import VoiceCallConfig
from .contracts import CallRecord, CallState, Conflict, Forbidden, RateLimited, TurnResult
from .cost import VoiceCostTracker
from .events import VoiceEventDispatcher
from .store import VoiceStore

MAX_UTTERANCE_CHARS = 1000
MAX_SPEECH_CHARS = 700

_HANGUP_RE = re.compile(
    r"^\s*(?:ok(?:ay)?[, ]+)?(?:good\s?bye|bye(?:\s+bye)?|hang up|end (?:the |this )?call|that'?s all(?: for now)?|"
    r"talk (?:to you )?later|au revoir|raccroche|[àa] plus tard|c'?est tout|salut)[.! ]*$", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MD_RE = re.compile(r"[*_`#>|~]+")
_BULLET_RE = re.compile(r"(?m)^\s*(?:[-•*]|\d+[.)])\s+")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF☀-➿️]")


def sanitize_utterance(text: str) -> str:
    """Normalise what the speech-to-text produced before it reaches Hood."""
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch if (ch in "\n\t" or unicodedata.category(ch)[0] != "C") else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()[:MAX_UTTERANCE_CHARS]


def speakable(text: str, limit: int = MAX_SPEECH_CHARS) -> str:
    """Turn a chat reply into something pleasant and safe to say aloud."""
    t = _CODE_FENCE_RE.sub(" (I left out some code; it's in the chat). ", text or "")
    t = _URL_RE.sub("a link", t)
    t = _BULLET_RE.sub("", t)
    t = _EMOJI_RE.sub("", t)
    t = _MD_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    cut = cut[:end + 1] if end > limit * 0.4 else cut.rsplit(" ", 1)[0]
    return cut.strip() + " I can go into more detail if you'd like."


class HoodVoiceBridge:
    def __init__(self, interaction: Any, engine: Any, store: VoiceStore, cost: VoiceCostTracker, cfg: VoiceCallConfig,
                 events: VoiceEventDispatcher, confirm: Any, *, clock: Callable[[], float] = time.time):
        self.interaction, self.engine, self.store, self.cost, self.cfg = interaction, engine, store, cost, cfg
        self.events, self.confirm, self.clock = events, confirm, clock
        self._recent: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def session_id(owner_id: str) -> str:
        return "user:" + owner_id

    # ------------------------------------------------------------------ limits
    def _admit_turn(self, call: CallRecord) -> None:
        now = self.clock()
        with self._lock:
            q = self._recent[call.call_id]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.cfg.max_turns_per_minute:
                raise RateLimited("Too many turns per minute on this call")
            q.append(now)
        fresh = self.store.get_call(call.call_id)
        if fresh is None or fresh.turns >= self.cfg.max_turns_per_call:
            raise RateLimited("This call reached its turn limit")

    # ------------------------------------------------------------------ the turn
    def handle_turn(self, call: CallRecord, text: str) -> TurnResult:
        if call.state not in (CallState.ACTIVE, CallState.CONNECTING):
            raise Conflict(f"Call is {call.state.value}")
        utterance = sanitize_utterance(text)
        if not utterance:
            return TurnResult(speech="", reason="empty")
        self._admit_turn(call)
        if _HANGUP_RE.match(utterance):
            self.store.update_call(call.call_id, turns=(self.store.get_call(call.call_id).turns or 0) + 1)
            return TurnResult(speech="Talk to you soon.", end_call=True, reason="hangup")

        sid = self.session_id(call.owner_id)
        cc = self.cost.cc
        before = len(cc.call_history) if cc is not None else 0
        t0 = self.clock()
        reply = self.interaction.handle_text_input(utterance, session_id=sid, channel="voice")
        reasoning = 0.0
        if cc is not None:
            reasoning = sum(float(c.get("cost_usd", 0.0)) for c in cc.call_history[before:]
                            if str(c.get("task_id") or "").startswith("conv_"))
        self.cost.record_usage(call.call_id, reasoning_usd=reasoning)
        fresh = self.store.get_call(call.call_id)
        self.store.update_call(call.call_id, turns=(fresh.turns if fresh else 0) + 1)

        result = TurnResult(speech=speakable(reply.text))
        self.events.emit(call.call_id, "turn", {"latency_ms": int((self.clock() - t0) * 1000), "reasoning_usd": round(reasoning, 6)})

        # A pending approval on a voice turn cannot happen by design; if one exists, never resolve it here.
        if getattr(reply, "approval_ref", None):
            self._request_confirmation(call, result, "approval", reply.approval_ref)
        if getattr(reply, "open_mission_draft", False):
            self._draft_mission(call, reply, result)
        return result

    # ------------------------------------------------------------------ helpers
    def _request_confirmation(self, call: CallRecord, result: TurnResult, kind: str, subject: str) -> None:
        item = {"kind": kind, "subject": subject}
        result.needs_confirmation.append(item)
        self.events.emit(call.call_id, "confirm_request", item, room=call.room, device_identity=call.device_id)
        if "phone" not in result.speech.lower():
            result.speech = (result.speech + " Please confirm that on your phone with Face ID.").strip()

    def _draft_mission(self, call: CallRecord, reply: Any, result: TurnResult) -> None:
        """Voice may draft a plan (nothing runs). Starting it needs the owner's device confirmation."""
        if self.engine is None:
            result.speech += " Missions aren't available right now."
            return
        sid = self.session_id(call.owner_id)
        try:
            draft = self.interaction.draft_mission_objective(sid, fallback=getattr(reply, "suggested_mission", "") or "")
            objective = draft["objective"]
            profile = self.interaction.guess_profile(objective)
            budget = min(self.cfg.mission_budget_cap_usd, self.cost.limits_for(call.owner_id)["per_call_usd"])
            mission = self.engine.create_mission(call.owner_id, objective, budget_usd=budget, profile=profile)
        except Exception as exc:                        # planning can fail (no model, stop latch, tools missing)
            result.speech = f"I couldn't draft that plan: {str(exc)[:160]}"
            result.reason = "mission_draft_failed"
            return
        # The engine reports a mission as its status(): {"mission_id": ..., "state": ...}. A plan that could not be
        # drafted (no model, tools missing, stop latch) comes back BLOCKED rather than raising.
        mission_id = mission.get("mission_id") or mission.get("id")
        if not mission_id or mission.get("state") != "AWAITING_PLAN_APPROVAL":
            why = str(mission.get("error") or "the plan could not be prepared")[:160]
            result.speech = f"I couldn't draft that plan: {why}"
            result.reason = "mission_draft_failed"
            return
        result.mission_id = mission_id
        result.speech = (f"I've drafted a plan with a spending limit of {budget:.2f} dollars. Nothing will run until "
                         "you confirm it on your phone with Face ID.")
        self._request_confirmation(call, result, "mission_plan", mission_id)
