"""END-TO-END: real LiveKit server + the real voice worker (subprocess, cascade mode) + the real Hood HTTP
server + a real WebRTC client playing the owner's phone.

REAL here: LiveKit server, token minting, agent dispatch, worker, silero voice-activity detection, LiveKit
Agents turn handling, Hood HTTP agent API, Hood's InteractionService, session state machine, cost ledger.
SIMULATED here: the speech models (a fake Hood STT returns a fixed transcript; a fake TTS returns a tone), the
model (scripted) and the audio the "phone" speaks (espeak-ng synthetic speech). It therefore proves the
plumbing and the Hood-in-the-loop behaviour, NOT recognition accuracy, voice quality or real-world latency.

Skipped unless livekit-server, espeak-ng and the livekit SDKs are available.
"""
import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
rtc = pytest.importorskip("livekit.rtc")
pytest.importorskip("livekit.agents")
pytest.importorskip("livekit.plugins.silero")
BIN = os.environ.get("HOOD_TEST_LIVEKIT_BIN") or shutil.which("livekit-server")
ESPEAK = shutil.which("espeak-ng")
pytestmark = pytest.mark.skipif(not (BIN and ESPEAK), reason="BLOCKED_EXTERNAL: needs livekit-server (HOOD_TEST_LIVEKIT_BIN) and espeak-ng")

from services.auth.auth_service import AuthenticationService                        # noqa: E402
from services.voicecall.config import Secret                                           # noqa: E402
from services.voicecall.contracts import CallState                                     # noqa: E402
from services.voicecall.livekit import LiveKitTransport                                # noqa: E402
from services.voicecall.policy import CallPermissionManager                            # noqa: E402
from services.voicecall.service import VoiceCallService                                # noqa: E402
from ui import routes as feature_routes                                                # noqa: E402
from ui.server import JarvisServer                                                     # noqa: E402
from tests.voicecall.conftest import OWNER                                             # noqa: E402
from tests.voicecall.test_livekit_real_server import KEY, SECRET, lk                  # noqa: E402,F401

REPO = Path(__file__).resolve().parents[2]


class FakeSpeech:
    """SIMULATED Hood speech service (STT/TTS endpoints). Tone length grows with the text so a long reply is long."""
    def __init__(self):
        self.stt_calls, self.tts_texts = 0, []

    def transcribe(self, user_id, audio_b64, mime):
        self.stt_calls += 1
        return "What is the status of my missions today?"

    def speak_audio(self, text, speaker="hood"):
        self.tts_texts.append(text)
        secs = min(0.6 + 0.12 * len(text.split()), 12.0)
        t = np.arange(int(24000 * secs)) / 24000.0
        pcm = (0.4 * 32767 * np.sin(2 * np.pi * 330 * t)).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        return buf.getvalue(), "audio/wav"


@pytest.fixture
def stack(lk, kit, tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    kit.cfg.livekit_url, kit.cfg.livekit_api_key, kit.cfg.livekit_api_secret = lk, KEY, Secret(SECRET)
    kit.cfg.provider = "gemini-cascade"
    real = LiveKitTransport(kit.cfg)
    kit.sessions.transport = real
    kit.events._transport = lambda: real
    kit.sessions.perms = CallPermissionManager(kit.store, kit.devices, auth, clock=kit.clock)
    kit.clock.__dict__["t"] = time.time()                       # real time: the worker, server and LiveKit run in real time
    kit.clock.advance = lambda s: None
    speech = FakeSpeech()
    server = JarvisServer(port=0, auth_service=auth, interaction_service=kit.interaction)
    svc = VoiceCallService(cfg=kit.cfg, store=kit.store, devices=kit.devices, transport=real, cost=kit.cost,
                           perms=kit.sessions.perms, events=kit.events, confirm=kit.confirm, bridge=kit.bridge,
                           sessions=kit.sessions, push=kit.push, policy=kit.policy, dispatcher=kit.dispatcher, speech=speech)
    feature_routes.SERVICES["voicecall"] = svc
    server.start()
    base = "http://127.0.0.1:" + str(server.httpd.server_address[1])
    kit.clock = type("RealClock", (), {"__call__": lambda self: time.time(), "advance": lambda self, s: None})()
    for obj in (kit.sessions, kit.cost, kit.bridge, kit.perms):
        obj.clock = kit.clock
    # The worker's WebSocket client honours HTTP(S)_PROXY even for loopback (seen in this sandbox: the proxy answers
    # 405), so a LOCAL LiveKit needs the proxy variables removed for the worker process.
    clean = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env = {**clean, "LIVEKIT_URL": lk, "LIVEKIT_API_KEY": KEY, "LIVEKIT_API_SECRET": SECRET, "HOOD_API_URL": base,
           "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1", "HOOD_VOICECALL_AGENT_NAME": kit.cfg.agent_name}
    log = tempfile.NamedTemporaryFile("w+", suffix="-worker.log", delete=False)
    worker = subprocess.Popen([sys.executable, "-m", "voice_agent.worker", "start"], cwd=str(REPO), env=env,
                              stdout=log, stderr=subprocess.STDOUT)
    deadline = time.time() + 40
    while time.time() < deadline:
        log.flush()
        if "registered worker" in Path(log.name).read_text():
            break
        if worker.poll() is not None:
            pytest.fail("worker exited early:\n" + Path(log.name).read_text()[-2000:])
        time.sleep(0.5)
    else:
        worker.kill()
        pytest.fail("worker never registered:\n" + Path(log.name).read_text()[-2000:])
    try:
        yield kit, speech, real, Path(log.name)
    finally:
        worker.terminate()
        try:
            worker.wait(10)
        except subprocess.TimeoutExpired:
            worker.kill()
        server.stop()
        feature_routes.SERVICES.pop("voicecall", None)


def _speech_frames(path, rate=16000):
    with wave.open(str(path), "rb") as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
        src = w.getframerate()
    x = np.interp(np.arange(0, len(raw), src / rate), np.arange(len(raw)), raw)
    return np.clip(x, -32768, 32767).astype("<i2")


QUESTION = Path(os.environ.get("HOOD_TEST_QUESTION_WAV", "/nonexistent"))


def _make_question():
    p = Path(tempfile.gettempdir()) / "hood-question.wav"
    subprocess.run([ESPEAK, "-v", "en-us", "-s", "150", "Hello Hood. What is the status of my missions today?", "-w", str(p)],
                   check=True)
    return p


class Phone:
    """A real WebRTC client standing in for the iPhone: joins with the granted token, speaks, listens."""
    def __init__(self):
        self.room = rtc.Room()
        self.agent_audio = []            # (timestamp, rms)
        self.source = rtc.AudioSource(16000, 1)
        self.events = []

    async def join(self, grant):
        self.room.on("track_subscribed", self._subscribed)
        self.room.on("data_received", lambda d: self.events.append(json.loads(bytes(d.data))))
        await self.room.connect(grant["url"], grant["token"])
        track = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        await self.room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    def _subscribed(self, track, pub, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.ensure_future(self._listen(track))

    async def _listen(self, track):
        async for ev in rtc.AudioStream(track, sample_rate=16000, num_channels=1):
            x = np.frombuffer(ev.frame.data, dtype="<i2").astype(np.float32)
            dur = ev.frame.samples_per_channel / float(ev.frame.sample_rate)        # real frame length, not an assumption
            self.agent_audio.append((time.time(), float(np.sqrt(np.mean(x * x))) if len(x) else 0.0, dur))

    def loud_seconds(self, since=0.0):
        return sum(d for t, r, d in self.agent_audio if t >= since and r > 500)

    async def speak(self, pcm):
        for i in range(0, len(pcm), 160):
            chunk = pcm[i:i + 160]
            if len(chunk) < 160:
                chunk = np.pad(chunk, (0, 160 - len(chunk)))
            await self.source.capture_frame(rtc.AudioFrame(chunk.tobytes(), 16000, 1, 160))
        silence = np.zeros(160, dtype="<i2").tobytes()
        for _ in range(120):                                       # 1.2 s of silence so the turn ends
            await self.source.capture_frame(rtc.AudioFrame(silence, 16000, 1, 160))


async def _until(pred, timeout, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return False


def test_call_end_to_end_through_the_real_worker(stack):
    kit, speech, transport, logpath = stack
    started = kit.sessions.start_owner_call(OWNER, kit.device)
    cid, grant = started["call"]["call_id"], started["join"]
    kit.router.text = "Two missions are running and one is waiting for your approval."
    question = _speech_frames(_make_question())

    async def scenario():
        phone = Phone()
        await phone.join(grant)
        # 1. the worker is dispatched into the room by the token, and its first heartbeat activates the call
        assert await _until(lambda: any(str(p.get("identity", "")).startswith("agent") or p["identity"] != kit.device_id
                                        for p in transport.list_participants(cid)), 40), logpath.read_text()[-3000:]
        assert await _until(lambda: kit.store.get_call(cid).state == CallState.ACTIVE, 30), logpath.read_text()[-3000:]
        # 2. the worker's opening line is synthesised by Hood's TTS and arrives as audio at the phone
        assert await _until(lambda: phone.loud_seconds() > 0.3, 30), (phone.agent_audio[-5:], logpath.read_text()[-2500:])
        # 3. the owner speaks; VAD -> STT -> Hood (the real InteractionService) -> TTS -> audio back
        before = time.time()
        n_requests = len(kit.router.requests)
        await phone.speak(question)
        assert await _until(lambda: len(kit.router.requests) > n_requests, 40), logpath.read_text()[-3000:]
        assert await _until(lambda: phone.loud_seconds(before) > 0.5, 40), logpath.read_text()[-3000:]
        # 4. the hang-up path: Hood ends the call and the room is torn down
        gone = asyncio.Event()
        phone.room.on("disconnected", lambda *_: gone.set())
        kit.sessions.end_call(cid)
        await asyncio.wait_for(gone.wait(), 15)
        return phone
    phone = asyncio.run(scenario())
    c = kit.store.get_call(cid)
    prompt = kit.router.requests[-1].prompt
    assert "LIVE PHONE CALL" in prompt and "What is the status of my missions today?" in prompt
    assert c.turns >= 1 and c.state == CallState.ENDED and c.last_heartbeat
    assert speech.stt_calls >= 1 and any("waiting for your approval" in t for t in speech.tts_texts)
    assert any(e["kind"] == "turn" for e in kit.store.events_after(cid))


def test_the_owner_can_interrupt_hood_while_it_speaks(stack):
    kit, speech, transport, logpath = stack
    started = kit.sessions.start_owner_call(OWNER, kit.device)
    cid, grant = started["call"]["call_id"], started["join"]
    sentence = "The first mission is a coffee shop website and it is being verified by the checker agent."
    kit.router.text = " ".join([sentence] * 3)                  # LiveKit speaks it sentence by sentence
    uninterrupted = 3 * (0.6 + 0.12 * len(sentence.split()))     # what the fake TTS would play if nobody cut in
    question = _speech_frames(_make_question())

    async def scenario():
        phone = Phone()
        await phone.join(grant)
        assert await _until(lambda: kit.store.get_call(cid).state == CallState.ACTIVE, 40), logpath.read_text()[-3000:]
        await _until(lambda: phone.loud_seconds() > 0.3, 30)               # let the (short) greeting finish
        await asyncio.sleep(3)
        await phone.speak(question)
        assert await _until(lambda: any(sentence in t for t in speech.tts_texts), 40), logpath.read_text()[-3000:]
        started_talking = time.time()
        assert await _until(lambda: phone.loud_seconds(started_talking) > 0.6, 20)
        kit.router.text = "Okay."                                       # whatever the owner says next gets a short answer
        await phone.speak(question)                                       # ... and the owner talks over Hood
        await asyncio.sleep(uninterrupted + 3)
        spoken = phone.loud_seconds(started_talking)
        kit.sessions.end_call(cid)
        return spoken
    spoken = asyncio.run(scenario())
    assert uninterrupted > 6
    assert spoken < uninterrupted * 0.85, f"Hood should have been cut off: spoke {spoken:.1f}s of {uninterrupted:.1f}s"
