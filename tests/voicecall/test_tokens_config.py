"""Token helpers, LiveKit join-token claims and configuration secrecy."""
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from services.voicecall.config import Secret, VoiceCallConfig
from services.voicecall.livekit import LiveKitTransport
from services.voicecall.tokens import (b64u, b64u_decode, jwt_es256, jwt_hs256, load_p256_public_key, verify_hs256,
                                       verify_p256)


def test_hs256_roundtrip_and_rejections():
    tok = jwt_hs256({"sub": "x", "exp": time.time() + 60}, "k" * 32)
    assert verify_hs256(tok, "k" * 32)["sub"] == "x"
    with pytest.raises(ValueError):
        verify_hs256(tok, "other" * 8)                                       # wrong secret
    with pytest.raises(ValueError):
        verify_hs256(jwt_hs256({"exp": time.time() - 5}, "k" * 32), "k" * 32)  # expired
    head, body, sig = tok.split(".")
    none_head = b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    with pytest.raises(ValueError):
        verify_hs256(f"{none_head}.{body}.", "k" * 32)                       # alg=none is not accepted
    with pytest.raises(ValueError):
        verify_hs256(f"{head}.{body}.{b64u(b'x' * 32)}", "k" * 32)           # forged signature
    with pytest.raises(ValueError):
        verify_hs256("not-a-jwt", "k" * 32)


def test_es256_is_raw_rs_and_verifies():
    key = ec.generate_private_key(ec.SECP256R1())
    tok = jwt_es256({"iss": "TEAM", "iat": 1}, key, {"kid": "KID"})
    head, body, sig = tok.split(".")
    assert json.loads(b64u_decode(head)) == {"alg": "ES256", "kid": "KID"}
    raw = b64u_decode(sig)
    assert len(raw) == 64                                                    # JWS ES256 = r||s, not DER
    assert verify_p256(key.public_key(), f"{head}.{body}".encode(), raw)
    assert not verify_p256(key.public_key(), b"tampered", raw)


def _transport():
    cfg = VoiceCallConfig(enabled=True, livekit_url="wss://p.livekit.cloud", livekit_api_key="APIKEY",
                          livekit_api_secret=Secret("S" * 40))
    return LiveKitTransport(cfg), cfg


def test_join_token_is_scoped_and_short_lived():
    t, cfg = _transport()
    g = t.mint_join_token(room="call_1", identity="dev_1", display_name="Owner", ttl_seconds=120, dispatch_agent=True,
                          agent_metadata='{"call_id":"call_1"}')
    claims = verify_hs256(g.token, "S" * 40)
    assert claims["iss"] == "APIKEY" and claims["sub"] == "dev_1"
    assert 100 <= claims["exp"] - time.time() <= 125                       # 120 s to connect
    v = claims["video"]
    assert v["room"] == "call_1" and v["roomJoin"] is True
    assert v["canPublishSources"] == ["microphone"]                          # audio only: no camera/screen
    for admin in ("roomAdmin", "roomCreate", "roomList", "roomRecord", "ingressAdmin", "hidden", "agent"):
        assert admin not in v, f"join token must not carry {admin}"
    assert claims["roomConfig"]["agents"] == [{"agentName": "hood-voice", "metadata": '{"call_id":"call_1"}'}]
    assert claims["roomConfig"]["maxParticipants"] == 3


def test_config_never_reveals_secrets():
    t, cfg = _transport()
    cfg.apns_key_path = "/secret/AuthKey.p8"
    text = repr(cfg) + str(cfg.public_view())
    assert "S" * 40 not in text and "AuthKey" not in text and "APIKEY" not in text
    assert repr(Secret("abc")) == "Secret(<set>)" and str(Secret("")) == "Secret(<unset>)"
    assert cfg.livekit_http_url == "https://p.livekit.cloud"


def test_env_loading_supports_file_secrets_and_clamps_limits(tmp_path, monkeypatch):
    secret_file = tmp_path / "lk_secret"
    secret_file.write_text("filesecret-value-123456789012345678901234\n")
    for k, v in {"HOOD_VOICECALL_ENABLED": "1", "LIVEKIT_URL": "ws://127.0.0.1:7880", "LIVEKIT_API_KEY": "k",
                 "LIVEKIT_API_SECRET_FILE": str(secret_file), "HOOD_VOICECALL_MAX_SECONDS": "999999",
                 "HOOD_VOICECALL_MAX_CALLS_PER_HOUR": "99"}.items():
        monkeypatch.setenv(k, v)
    cfg = VoiceCallConfig.from_env(tmp_path)
    assert cfg.enabled and cfg.transport_ready
    assert cfg.livekit_api_secret.reveal().startswith("filesecret")
    assert cfg.max_call_seconds == 3600 and cfg.max_calls_per_hour == 6      # hard ceilings
    assert len(cfg.agent_secret.reveal()) >= 32
    p = tmp_path / "voicecall" / ".agent_secret"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert VoiceCallConfig.from_env(tmp_path).agent_secret.reveal() == cfg.agent_secret.reveal()   # stable


def test_unconfigured_transport_refuses():
    from services.voicecall.livekit import TransportError
    with pytest.raises(TransportError):
        LiveKitTransport(VoiceCallConfig())
