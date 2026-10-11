"""The voice worker's own logic, against Hood's real HTTP agent API (SIMULATED: transport, model, push).
No LiveKit server or speech model is involved here; see test_worker_end_to_end.py for the transport."""
import asyncio

import pytest

pytest.importorskip("livekit.agents", reason="livekit-agents not installed")

from voice_agent.agent import HoodBridge, build_agent, is_bridged                       # noqa: E402
from voice_agent.hood_client import HoodClient, HoodError                                # noqa: E402
from voice_agent.providers import wav_to_pcm                                             # noqa: E402
from voice_agent.usage import UsageMeter                                                 # noqa: E402
from tests.voicecall.test_voice_http import web, agent_auth, call, dev_auth              # noqa: E402,F401


def _client(base, kit, cid):
    return HoodClient(base, agent_auth(kit, cid)[7:])


def _call(web_):
    base, cookies, kit, svc = web_
    cid = call(base, "POST", "/api/voicecall/calls/start", dev_auth(kit), {})[1]["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    return base, kit, cid


def test_client_talks_to_the_real_agent_api(web):
    base, kit, cid = _call(web)
    c = _client(base, kit, cid)
    info = c.session()
    assert info["call_id"] == cid and info["mode"] == "realtime" and info["model"] == "gemini-3.8-live"
    assert c.heartbeat({"audio_in_s": 2})["continue"] is True
    kit.router.text = "Two missions are running."
    r = c.turn("what's running?")
    assert r.speech == "Two missions are running." and not r.end_call
    assert c.turn("goodbye").end_call is True
    c.end("hood_hangup")
    assert kit.store.get_call(cid).state.terminal
    with pytest.raises(HoodError) as e:
        c.turn("hello?")
    assert e.value.status == 403          # the call is over: the credential is dead


def test_client_with_a_wrong_credential_or_unreachable_hood_raises_clean_errors(web):
    base, kit, cid = _call(web)
    with pytest.raises(HoodError) as e:
        HoodClient(base, "not-a-token").session()
    assert e.value.status == 401
    with pytest.raises(HoodError) as e:
        HoodClient("http://127.0.0.1:1", "x", timeout=1).session()
    assert e.value.status == 0


def test_bridge_returns_hoods_words_and_survives_hood_errors(web):
    base, kit, cid = _call(web)
    kit.router.text = "Your plan is ready."
    meter = UsageMeter()
    b = HoodBridge(_client(base, kit, cid), meter)
    assert asyncio.run(b.ask("how is it going")) == "Your plan is ready."
    assert b.replies == ["Your plan is ready."]
    kit.sessions.end_call(cid)
    spoken = asyncio.run(b.ask("still there?"))                                        # Hood refuses (call over)
    assert "couldn't reach Hood" in spoken                                              # a safe sentence, no crash


def test_realtime_agent_has_exactly_one_tool_and_returns_a_say_exactly_instruction(web):
    base, kit, cid = _call(web)
    kit.router.text = "All clear."
    b = HoodBridge(_client(base, kit, cid), UsageMeter())
    agent = build_agent(b, {"language": "fr"}, "realtime")
    assert [t.id for t in agent.tools] == ["ask_hood"]
    assert "word for word" in agent.instructions and "fr" in agent.instructions
    out = asyncio.run(agent.tools[0]._func(agent, None, "bonjour"))                     # call the tool body directly
    assert out == 'Say exactly this, word for word, and nothing else: "All clear."'


def test_cascade_agent_has_no_tools_and_stops_the_default_llm_reply(web):
    from livekit.agents.llm import StopResponse
    base, kit, cid = _call(web)
    kit.router.text = "I hear you."
    b = HoodBridge(_client(base, kit, cid), UsageMeter())
    agent = build_agent(b, {}, "cascade")
    assert list(agent.tools) == []
    said = []

    class FakeSession:
        async def say(self, text, **kw):
            said.append(text)

    class Msg:
        text_content = "hello hood"
    agent._activity = None
    object.__setattr__(agent, "_session", FakeSession()) if hasattr(agent, "_session") else None
    try:
        type(agent).session = property(lambda self: FakeSession())
    except Exception:
        pass
    with pytest.raises(StopResponse):
        asyncio.run(agent.on_user_turn_completed(None, Msg()))
    assert said == ["I hear you."]


def test_integrity_check_detects_speech_that_did_not_come_from_hood():
    hood = ["Your plan for the coffee shop website is ready and waiting for your confirmation."]
    assert is_bridged("Your plan for the coffee shop website is ready and waiting for your confirmation.", hood)
    assert is_bridged("Okay, sure", hood)                                                # short filler
    assert not is_bridged("I have gone ahead and approved the plan and started the mission for you right now.", hood)
    assert not is_bridged("Transferring the funds to the supplier account as requested by the owner today.", hood)


def test_unbridged_speech_is_reported_and_the_server_ends_the_call(web):
    base, kit, cid = _call(web)
    c = _client(base, kit, cid)
    meter = UsageMeter()
    b = HoodBridge(c, meter)
    for _ in range(3):
        b.check_spoken("I went ahead and approved everything on your behalf without asking anyone at all.")
    verdict = c.heartbeat(meter.drain())
    assert verdict["continue"] is False and verdict["reason"] == "voice_integrity"
    assert kit.store.get_call(cid).state.terminal
    assert any(e["kind"] == "unbridged_speech" for e in kit.store.events_after(cid))


def test_usage_meter_prefers_measured_tokens_and_reports_deltas():
    from types import SimpleNamespace as NS
    t = [0.0]
    m = UsageMeter(clock=lambda: t[0])
    t[0] = 10
    d = m.drain()                                         # no metrics yet: wall-clock fallback
    assert d["audio_in_s"] == pytest.approx(10) and "text_in_tokens" not in d
    m.on_metrics(NS(type="realtime_model_metrics", input_token_details=NS(audio_tokens=250, text_tokens=40),
                    output_token_details=NS(audio_tokens=125, text_tokens=20)))
    d = m.drain()
    assert d["audio_in_s"] == 10 and d["audio_out_s"] == 5 and d["text_in_tokens"] == 40 and d["text_out_tokens"] == 20
    d2 = m.drain()
    assert d2["audio_in_s"] == 0 and d2["audio_out_s"] == 0                              # deltas, not running totals
    m.on_metrics(NS(type="llm_metrics"))                                                 # other metrics are ignored
    assert m.drain()["audio_in_s"] == 0


def test_wav_to_pcm_accepts_wav_and_refuses_other_audio(tmp_path):
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x01" * 2400)
    pcm, rate, ch = wav_to_pcm(buf.getvalue())
    assert rate == 24000 and ch == 1 and len(pcm) == 4800
    with pytest.raises(ValueError, match="non-WAV"):
        wav_to_pcm(b"ID3\x04\x00mp3-data")
