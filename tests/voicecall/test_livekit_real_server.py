"""REAL LiveKit server (v1.x binary started locally) + real WebRTC signalling clients (livekit rtc SDK).

Verifies, against the actual server: our hand-built access tokens authenticate; token tampering and expiry
are rejected; room control (create/list/send-data/remove/delete) works; agent dispatch is requested through
the token; and VoiceSessionManager enforces its rules on a real room (verified connection, unexpected
participant removal, room teardown on hang-up).

Skipped (explicit reason) unless a ``livekit-server`` binary is available via HOOD_TEST_LIVEKIT_BIN or PATH
and the ``livekit`` python SDK is installed. No audio is exchanged: this tests transport, auth and control,
not speech.
"""
import asyncio
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request

import pytest

BIN = os.environ.get("HOOD_TEST_LIVEKIT_BIN") or shutil.which("livekit-server")
rtc = pytest.importorskip("livekit.rtc", reason="livekit python SDK not installed")
pytestmark = pytest.mark.skipif(not BIN, reason="BLOCKED_EXTERNAL: no livekit-server binary (set HOOD_TEST_LIVEKIT_BIN)")

from services.voicecall.config import Secret, VoiceCallConfig            # noqa: E402
from services.voicecall.contracts import CallState                        # noqa: E402
from services.voicecall.livekit import LiveKitTransport, TransportError   # noqa: E402
from tests.voicecall.conftest import OWNER                                # noqa: E402

KEY, SECRET = "testkey", "testsecret-0123456789-0123456789-0123456789"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def lk(tmp_path_factory):
    d = tmp_path_factory.mktemp("lk")
    http, tcp, udp = _free_port(), _free_port(), _free_port()
    cfg = d / "lk.yaml"
    # development: true also switches on the single-node agent (worker) service the end-to-end test needs
    cfg.write_text(f"development: true\nport: {http}\nbind_addresses: ['127.0.0.1']\nrtc:\n  tcp_port: {tcp}\n  udp_port: {udp}\n"
                   f"  use_external_ip: false\n  node_ip: 127.0.0.1\nkeys:\n  {KEY}: {SECRET}\nlogging:\n  level: warn\n")
    proc = subprocess.Popen([BIN, "--config", str(cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{http}/", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("livekit-server did not start")
    yield f"ws://127.0.0.1:{http}"
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _transport(url):
    cfg = VoiceCallConfig(enabled=True, livekit_url=url, livekit_api_key=KEY, livekit_api_secret=Secret(SECRET))
    return LiveKitTransport(cfg), cfg


def run(coro):
    return asyncio.run(coro)


def test_our_tokens_authenticate_and_room_control_works(lk):
    t, _ = _transport(lk)
    room = "room_ctrl_1"
    created = t.create_room(room, dispatch_agent=True, agent_metadata='{"x":1}')
    assert created["name"] == room and created["max_participants"] == 3
    g = t.mint_join_token(room=room, identity="device-1", display_name="Owner", ttl_seconds=60)

    async def scenario():
        r = rtc.Room()
        got = []
        r.on("data_received", lambda d: got.append(json.loads(bytes(d.data))))
        await r.connect(g.url, g.token)
        assert r.local_participant.identity == "device-1" and r.name == room
        assert [p["identity"] for p in t.list_participants(room)] == ["device-1"]
        assert t.send_data(room, {"type": "confirm_request", "kind": "mission_plan"}, destination_identities=["device-1"])
        await asyncio.sleep(1.0)
        assert got == [{"type": "confirm_request", "kind": "mission_plan"}]
        assert t.remove_participant(room, "device-1") is True
        await asyncio.sleep(0.5)
        await r.disconnect()
    run(scenario())
    assert t.delete_room(room) is True and t.delete_room(room) is True            # idempotent
    assert t.list_participants(room) == []


def test_tampered_expired_and_foreign_signed_tokens_are_rejected(lk):
    t, cfg = _transport(lk)
    good = t.mint_join_token(room="room_sec_1", identity="d", display_name="x", ttl_seconds=60)
    expired = t.mint_join_token(room="room_sec_1", identity="d", display_name="x", ttl_seconds=-120)
    head, body, sig = good.token.split(".")
    forged = f"{head}.{body}.{sig[:-3]}AAA"
    other_secret = LiveKitTransport(VoiceCallConfig(enabled=True, livekit_url=lk, livekit_api_key=KEY,
                                                    livekit_api_secret=Secret("another-secret-0123456789-0123456789-01")))
    foreign = other_secret.mint_join_token(room="room_sec_1", identity="d", display_name="x", ttl_seconds=60)

    async def attempt(token):
        r = rtc.Room()
        try:
            await r.connect(lk, token)
        except Exception:
            return False
        await r.disconnect()
        return True

    assert run(attempt(good.token)) is True                      # control: a valid token does connect
    assert run(attempt(forged)) is False
    assert run(attempt(expired.token)) is False
    assert run(attempt(foreign.token)) is False


def test_admin_calls_need_the_secret(lk):
    bad = LiveKitTransport(VoiceCallConfig(enabled=True, livekit_url=lk, livekit_api_key=KEY,
                                           livekit_api_secret=Secret("wrong-secret-0123456789-0123456789-01234")))
    with pytest.raises(TransportError):
        bad.create_room("room_nope")


def test_join_token_requests_agent_dispatch_with_metadata(lk):
    t, _ = _transport(lk)
    room, meta = "room_dispatch_1", '{"call_id":"call_x"}'
    g = t.mint_join_token(room=room, identity="dev", display_name="o", ttl_seconds=60, dispatch_agent=True, agent_metadata=meta)

    async def scenario():
        r = rtc.Room()
        await r.connect(g.url, g.token)
        await asyncio.sleep(0.5)
        req = urllib.request.Request(
            lk.replace("ws://", "http://") + "/twirp/livekit.AgentDispatchService/ListDispatch",
            data=json.dumps({"room": room}).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + t._admin_token(room)})
        out = json.loads(urllib.request.urlopen(req, timeout=5).read())
        await r.disconnect()
        return out
    out = run(scenario())
    disp = out.get("agent_dispatches") or out.get("agentDispatches") or []
    assert disp and disp[0]["agent_name" if "agent_name" in disp[0] else "agentName"] == "hood-voice"
    assert disp[0]["metadata"] == meta
    t.delete_room(room)


def test_session_manager_on_a_real_room(lk, kit):
    """The in-memory fixtures, but with the REAL transport: join verification, intruder removal, teardown."""
    t, cfg = _transport(lk)
    kit.sessions.transport = t
    kit.events._transport = lambda: t
    kit.cfg.livekit_url, kit.cfg.livekit_api_key = lk, KEY
    kit.cfg.livekit_api_secret = Secret(SECRET)
    started = kit.sessions.start_owner_call(OWNER, kit.device)
    cid, grant = started["call"]["call_id"], started["join"]

    async def scenario():
        owner = rtc.Room()
        await owner.connect(grant["url"], grant["token"])
        info = kit.sessions.confirm_connected(cid, kit.device_id)                # verified against the real participant list
        assert info["state"] == "ACTIVE"
        intruder = t.mint_join_token(room=cid, identity="intruder", display_name="x", ttl_seconds=60)
        ir = rtc.Room()
        await ir.connect(intruder.url, intruder.token)
        await asyncio.sleep(0.5)
        removed = kit.sessions.sweep()
        assert "removed:intruder" in removed
        await asyncio.sleep(0.5)
        assert [p["identity"] for p in t.list_participants(cid)] == [kit.device_id]
        gone = asyncio.Event()
        owner.on("disconnected", lambda *_: gone.set())
        kit.sessions.end_call(cid)                                              # hang-up tears the room down server-side
        await asyncio.wait_for(gone.wait(), 10)
        await ir.disconnect()
    run(scenario())
    c = kit.store.get_call(cid)
    assert c.state == CallState.ENDED and t.list_participants(cid) == []
