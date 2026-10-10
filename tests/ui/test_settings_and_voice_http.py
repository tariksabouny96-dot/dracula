"""Settings > Model provider and the Voice page over real HTTP.

The owner sets the Gemini key and prices in the UI (no .env editing, no
restart); the key is never echoed back. Voice transcribes and speaks through
Gemini only with a key, a price, consent (for microphone audio), and no
emergency stop. Google is faked at the HTTP boundary; nothing leaves the machine.
"""
import base64
import io
import json
import wave

import pytest
from cryptography.fernet import Fernet

from packages.auth.vault import SecretVault
from packages.contracts import ModelResponse, ModelUsage, ProviderName
from services.auth.auth_service import AuthenticationService, UserRole
from services.model_gateway.gemini_adapter import GeminiProviderAdapter
from services.model_gateway.router import ModelRouter
from services.voice import cloud
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request

KEY = "test-gemini-key-not-real-0123456789abcd"


@pytest.fixture
def stack(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HOOD_GEMINI_CREDENTIAL", "HOOD_MODEL_PRICING"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    routes.load_modules()
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "viewer", "Viewer", "ViewerPassword123!", UserRole.VIEWER)
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    router = ModelRouter(price_table={})
    vault = SecretVault(tmp_path / "vault.enc", master_key=Fernet.generate_key())
    router.providers[ProviderName.GEMINI] = GeminiProviderAdapter(vault=vault)
    for name in ("router", "voice", "firewall", "emergency_stop"):
        routes.SERVICES.pop(name, None)
    routes.SERVICES["router"] = router
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
    viewer = "hood_session=" + auth.authenticate("viewer", "ViewerPassword123!").session_token
    try:
        yield base, owner, viewer, router, tmp_path
    finally:
        srv.stop()
        for name in ("router", "voice"):
            routes.SERVICES.pop(name, None)


def j(resp):
    return json.loads(resp[1])


def test_owner_sets_key_and_prices_in_settings_without_restart(stack):
    base, owner, viewer, router, tmp = stack
    gemini = router.providers[ProviderName.GEMINI]
    assert request(base, "/api/settings/model", viewer)[0] == 403
    s = j(request(base, "/api/settings/model", owner))
    assert s["key"]["set"] is False and s["state"] == "not_configured" and s["pricing"]["missing"]

    assert request(base, "/api/settings/model/key", viewer, {"api_key": KEY, "confirm": True})[0] == 403
    assert request(base, "/api/settings/model/key", owner, {"api_key": KEY})[0] == 400          # no confirm
    assert request(base, "/api/settings/model/key", owner, {"api_key": "x y", "confirm": True})[0] == 400
    assert request(base, "/api/settings/model/key", owner, {"api_key": KEY, "confirm": True}, csrf=False)[0] == 403
    r = request(base, "/api/settings/model/key", owner, {"api_key": KEY, "confirm": True})
    assert r[0] == 200 and KEY.encode() not in r[1]
    assert gemini.is_healthy()                                   # in effect immediately
    body = request(base, "/api/settings/model", owner)[1]
    assert KEY.encode() not in body and json.loads(body)["key"] == {"set": True, "source": "settings", "hint": "…abcd"}
    assert json.loads(body)["state"] == "needs_pricing"

    assert request(base, "/api/settings/model/pricing", owner, {"mode": "free"})[0] == 400       # no confirm
    bad = {"mode": "custom", "confirm": True, "prices": {m: {"input_per_1k_usd": -1, "output_per_1k_usd": 1}
                                                          for m in s["pricing"]["models"]}}
    assert request(base, "/api/settings/model/pricing", owner, bad)[0] == 400
    r = request(base, "/api/settings/model/pricing", owner, {"mode": "free", "confirm": True})
    assert r[0] == 200 and j(r)["pricing"]["missing"] == [] and j(r)["pricing"]["source"] == "Settings"
    assert (tmp / "data" / "model_pricing.json").is_file()
    assert cloud.tts_model() in router.price_table["gemini"]    # voice models are priced too
    assert j(request(base, "/api/settings/model", owner))["state"] == "configured"

    r = request(base, "/api/settings/model/key/remove", owner, {"confirm": True})
    assert r[0] == 200 and j(r)["key"]["set"] is False and not gemini.is_healthy()


def test_connection_check_makes_one_governed_call(stack, monkeypatch):
    base, owner, _, router, _ = stack
    request(base, "/api/settings/model/key", owner, {"api_key": KEY, "confirm": True})
    r = j(request(base, "/api/settings/model/test", owner, {}))
    assert r["ok"] is False and "pricing" in r["error"].lower()            # refused: unknown cost
    request(base, "/api/settings/model/pricing", owner, {"mode": "free", "confirm": True})
    gemini = router.providers[ProviderName.GEMINI]
    monkeypatch.setattr(gemini, "invoke", lambda req: ModelResponse(
        text="OK", provider=ProviderName.GEMINI, model_name="gemini-3.5-flash-lite",
        usage=ModelUsage(prompt_tokens=5, completion_tokens=1, total_tokens=6), latency_ms=42,
        is_mock=False, is_fallback=False))
    r = j(request(base, "/api/settings/model/test", owner, {}))
    assert r == {"ok": True, "model": "gemini-3.5-flash-lite", "latency_ms": 42, "reply": "OK"}
    assert j(request(base, "/api/settings/model", owner))["state"] == "verified_online"


class FakeResp:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.data


def test_voice_transcribes_and_speaks_only_when_allowed(stack, monkeypatch):
    base, owner, _, router, _ = stack
    sent = []
    real_urlopen = cloud.urllib.request.urlopen

    def fake_urlopen(req, timeout=0):
        if cloud.HOST not in getattr(req, "full_url", str(req)):
            return real_urlopen(req, timeout=timeout)          # the test's own calls to HOOD
        body = json.loads(req.data)
        sent.append((req.full_url, body))
        usage = {"promptTokenCount": 10, "candidatesTokenCount": 4, "totalTokenCount": 14}
        if "responseModalities" in body.get("generationConfig", {}):
            pcm = b"\x00\x01" * 2400
            return FakeResp({"candidates": [{"content": {"parts": [{"inlineData": {
                "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}],
                "usageMetadata": usage})
        return FakeResp({"candidates": [{"content": {"parts": [{"text": "hello hood"}]}}], "usageMetadata": usage})

    monkeypatch.setattr(cloud.urllib.request, "urlopen", fake_urlopen)
    audio = {"audio_b64": base64.b64encode(b"webm-bytes").decode(), "mime_type": "audio/webm"}

    st = j(request(base, "/api/voice/status", owner))
    assert st["state"] == "not_configured" and st["consent"] is False
    request(base, "/api/settings/model/key", owner, {"api_key": KEY, "confirm": True})
    r = request(base, "/api/voice/speak", owner, {"text": "Hello"})
    assert r[0] == 400 and b"price" in r[1]                                  # unknown cost: refused
    request(base, "/api/settings/model/pricing", owner, {"mode": "free", "confirm": True})
    assert j(request(base, "/api/voice/status", owner))["state"] == "available"

    r = request(base, "/api/voice/transcribe", owner, audio)
    assert r[0] == 400 and b"consent" in r[1] and not sent                   # no audio sent without consent
    assert request(base, "/api/voice/consent", owner, {})[0] == 400          # explicit confirm needed
    assert j(request(base, "/api/voice/consent", owner, {"confirm": True})) == {"consent": True}

    assert request(base, "/api/voice/transcribe", owner, {**audio, "mime_type": "text/html"})[0] == 400
    r = request(base, "/api/voice/transcribe", owner, audio)
    assert r[0] == 200 and j(r) == {"transcript": "hello hood"}
    assert sent[-1][0].endswith(cloud.stt_model() + ":generateContent")
    assert sent[-1][1]["contents"][0]["parts"][0]["inline_data"]["mime_type"] == "audio/webm"

    status, wav_bytes, headers = request(base, "/api/voice/speak", owner, {"text": "Hello"})
    assert status == 200 and headers["Content-Type"] == "audio/wav"
    with wave.open(io.BytesIO(wav_bytes)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (24000, 1, 2400)

    class Stop:
        is_active, stop_reason = True, "owner stop"
    routes.SERVICES["voice"].emergency_stop = Stop()
    assert request(base, "/api/voice/speak", owner, {"text": "Hello"})[0] == 423

    r = j(request(base, "/api/voice/consent", owner, {"confirm": True, "revoke": True}))
    assert r == {"consent": False}


class AudioResp(FakeResp):
    def __init__(self, data):
        self.data = data


def test_elevenlabs_voice_from_settings(stack, monkeypatch):
    import urllib.error
    from services.firewall.policy import NetworkFirewall
    from services.voice import elevenlabs as eleven
    base, owner, viewer, router, tmp = stack
    routes.SERVICES["firewall"] = NetworkFirewall(tmp / "fw")
    try:
        request(base, "/api/settings/model/key", owner, {"api_key": KEY, "confirm": True})
        request(base, "/api/settings/model/pricing", owner, {"mode": "free", "confirm": True})
        calls, real_urlopen, mode = [], cloud.urllib.request.urlopen, {"status": 200}
        el_key = "elevenlabs-test-key-not-real-0001"

        def fake_urlopen(req, timeout=0):
            if eleven.HOST not in getattr(req, "full_url", str(req)):
                return real_urlopen(req, timeout=timeout)
            calls.append(req)
            if mode["status"] != 200:
                raise urllib.error.HTTPError(req.full_url, mode["status"], "x", {},
                                             io.BytesIO(b'{"detail": {"message": "invalid api key"}}'))
            return AudioResp(b"ID3fake-mp3-bytes")

        monkeypatch.setattr(eleven.urllib.request, "urlopen", fake_urlopen)
        v = j(request(base, "/api/settings/voice", owner))
        assert v["tts_provider"] == "gemini" and v["key"]["set"] is False
        assert request(base, "/api/settings/voice", viewer)[0] == 403
        assert request(base, "/api/settings/voice", owner, {"tts_provider": "elevenlabs", "confirm": True})[0] == 400

        r = request(base, "/api/settings/voice/key", owner, {"api_key": el_key, "confirm": True})
        assert r[0] == 200 and el_key.encode() not in r[1] and j(r)["key"]["hint"] == "…0001"
        assert any(rule["host"] == eleven.HOST for rule in routes.SERVICES["firewall"].list_rules())

        hood_voice, x_voice = "21m00Tcm4TlvDq8ikWAM", "AZnzlk1XvdvUeBnXmlld"
        r = request(base, "/api/settings/voice", owner, {"tts_provider": "elevenlabs", "voice_id": hood_voice,
                                                         "x_voice_id": x_voice, "confirm": True})
        assert r[0] == 200
        r = request(base, "/api/voice/speak", owner, {"text": "Hello"})
        assert r[0] == 400 and b"price" in r[1] and not calls            # unknown cost: refused, nothing sent

        request(base, "/api/settings/voice", owner, {"price_per_1k_chars": 0, "confirm": True})
        status, audio, headers = request(base, "/api/voice/speak", owner, {"text": "Hello Zak"})
        assert status == 200 and headers["Content-Type"] == "audio/mpeg" and audio == b"ID3fake-mp3-bytes"
        sent = calls[-1]
        assert f"/v1/text-to-speech/{hood_voice}?output_format=" in sent.full_url
        assert sent.headers["Xi-api-key"] == el_key
        assert json.loads(sent.data) == {"text": "Hello Zak", "model_id": eleven.DEFAULT_MODEL}
        request(base, "/api/voice/speak", owner, {"text": "This is X.", "speaker": "x"})
        assert f"/text-to-speech/{x_voice}?" in calls[-1].full_url           # X speaks with its own voice

        st = j(request(base, "/api/voice/status", owner))
        assert st["tts_provider"] == "elevenlabs" and "ElevenLabs" in st["provider"] and st["state"] == "available"

        mode["status"] = 401
        r = request(base, "/api/voice/speak", owner, {"text": "Hello"})
        assert r[0] == 503 and b"rejected" in r[1]

        r = request(base, "/api/settings/voice/key/remove", owner, {"confirm": True})
        assert j(r)["key"]["set"] is False and j(r)["tts_provider"] == "gemini"
    finally:
        routes.SERVICES.pop("firewall", None)
