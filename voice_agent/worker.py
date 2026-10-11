"""Entrypoint: ``python -m voice_agent.worker dev|start`` (LiveKit Agents CLI).

Environment (secrets via *_FILE are supported for every key):
  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   the LiveKit project the calls run on
  HOOD_API_URL                                       where Hood listens (private network), e.g. http://127.0.0.1:8990
  GOOGLE_API_KEY | GEMINI_API_KEY                    Gemini Live key (realtime mode)
  HOOD_VOICECALL_AGENT_NAME                          must match Hood's agent name (default hood-voice)
Hood passes the per-call credential in the dispatch metadata; no Hood secret is stored here.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from livekit import agents
from livekit.agents import AgentServer, AgentSession, JobContext

from .agent import HoodBridge, build_agent
from .hood_client import HoodClient, HoodError
from .providers import UNTESTED, build_cascade, build_realtime_model
from .usage import UsageMeter

log = logging.getLogger("hood.voice_agent")
HEARTBEAT_SECONDS = 10
END_MESSAGES = {"max_duration": "We've reached the time limit for this call. Goodbye.",
                "budget": "We've reached the spending limit for this call. Goodbye.",
                "emergency": "Goodbye."}

server = AgentServer()


@server.rtc_session(agent_name=os.environ.get("HOOD_VOICECALL_AGENT_NAME", "hood-voice"))
async def entrypoint(ctx: JobContext) -> None:
    meta = json.loads(ctx.job.metadata or "{}")
    hood = HoodClient(os.environ.get("HOOD_API_URL", "http://127.0.0.1:8990"), meta["agent_token"])
    info = await asyncio.to_thread(hood.session)
    meter = UsageMeter()
    bridge = HoodBridge(hood, meter)
    mode = info.get("mode", "realtime")
    agent = build_agent(bridge, info, mode)
    if mode == "realtime":
        session = AgentSession(llm=build_realtime_model(info, agent.instructions))
    else:
        from livekit.plugins import silero
        stt_, tts_ = build_cascade(hood)
        session = AgentSession(stt=stt_, tts=tts_, vad=silero.VAD.load())

    @session.on("metrics_collected")
    def _metrics(ev):
        meter.on_metrics(ev.metrics)

    @session.on("agent_state_changed")
    def _state(ev):
        meter.agent_state(ev.new_state)
        if ev.new_state == "listening" and bridge.pending_end:
            asyncio.create_task(_hang_up("hood_hangup"))

    @session.on("conversation_item_added")
    def _item(ev):
        item = ev.item
        if getattr(item, "role", "") == "assistant" and mode == "realtime":
            text = getattr(item, "text_content", "") or ""
            if text and not bridge.check_spoken(text):
                log.warning("assistant spoke text that did not come from Hood")

    done = asyncio.Event()

    async def _hang_up(reason: str, message: str = "") -> None:
        if done.is_set():
            return
        done.set()
        try:
            if message:
                await session.say(message) if mode != "realtime" else await session.generate_reply(
                    instructions=f'Say exactly this and nothing else: "{message}"')
            await asyncio.to_thread(hood.end, reason)
        except Exception:
            pass
        ctx.shutdown(reason=reason)

    async def _heartbeats() -> None:
        while not done.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                verdict = await asyncio.to_thread(hood.heartbeat, meter.drain())
            except HoodError as exc:
                if exc.status in (401, 403, 409):         # the call is over or the credential is gone
                    await _hang_up("hood_hangup")
                    return
                continue
            if not verdict.get("continue", True):
                reason = verdict.get("reason", "")
                await _hang_up(reason or "budget", END_MESSAGES.get(reason, "Goodbye."))
                return

    await ctx.connect()
    await session.start(room=ctx.room, agent=agent)
    await asyncio.to_thread(hood.heartbeat, meter.drain())           # first heartbeat marks the call ACTIVE
    asyncio.create_task(_heartbeats())

    @ctx.room.on("participant_disconnected")
    def _left(p):
        if not done.is_set():
            asyncio.create_task(_after_owner_leaves())

    async def _after_owner_leaves() -> None:
        await asyncio.sleep(20)                                       # allow a reconnect
        if not any(not str(x.identity).startswith("agent") for x in ctx.room.remote_participants.values()):
            await _hang_up("network")

    opening = info.get("briefing") or "Hood here. How can I help?"
    if mode == "realtime":
        await session.generate_reply(instructions=f'Say exactly this and nothing else: "{opening}"')
    else:
        await session.say(opening)
    if info.get("language") in UNTESTED:
        log.info("language %s is untested for voice", info.get("language"))


if __name__ == "__main__":
    agents.cli.run_app(server)
