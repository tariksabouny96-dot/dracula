"""Voice egress against Hood's REAL NetworkFirewall (default-deny). Guards against a mismatch between what the voice
module seeds/asks and what the firewall actually allows (a swallowed seeding error would silently block every call)."""
import pytest

from services.firewall.policy import FirewallBlocked, NetworkFirewall
from services.voicecall.config import Secret, VoiceCallConfig
from services.voicecall.livekit import LiveKitTransport, TransportError
from services.voicecall.push import PROD_HOST, SANDBOX_HOST, PushNotificationService
from services.voicecall.wiring import _seed_firewall
from tests.voicecall.test_push_apns import DEVICE, PEM, RecordingTransport


def _cfg(**kw):
    base = dict(enabled=True, livekit_url="wss://hood-demo.livekit.cloud", livekit_api_key="k", livekit_api_secret=Secret("s" * 40),
                apns_team_id="TEAM123456", apns_key_id="KEY1234567", apns_key_path="/x.p8", apns_bundle_id="com.example.hood")
    base.update(kw)
    return VoiceCallConfig(**base)


def test_seeding_opens_exactly_livekit_and_apple_push_and_nothing_else(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    _seed_firewall(fw, _cfg())
    for host in ("hood-demo.livekit.cloud", PROD_HOST, SANDBOX_HOST):
        assert fw.authorize(host, 443).allowed, host
    assert not fw.authorize("hood-demo.livekit.cloud", 80).allowed          # only the port that is needed
    assert not fw.authorize("evil.example.com", 443).allowed
    assert not fw.authorize("generativelanguage.googleapis.com", 443).allowed   # the worker talks to Google, not Hood


def test_apple_hosts_are_not_opened_until_apns_is_configured(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    _seed_firewall(fw, _cfg(apns_team_id="", apns_key_id="", apns_key_path="", apns_bundle_id=""))
    assert fw.authorize("hood-demo.livekit.cloud", 443).allowed
    assert not fw.authorize(PROD_HOST, 443).allowed and not fw.authorize(SANDBOX_HOST, 443).allowed


def test_push_and_livekit_pass_the_real_firewall_once_seeded_and_are_blocked_before(tmp_path):
    cfg = _cfg()
    fw = NetworkFirewall(tmp_path / "fw")
    push = PushNotificationService(cfg, transport=RecordingTransport(), firewall=fw, key_pem=PEM)
    with pytest.raises(FirewallBlocked):                                    # default deny until seeded
        push.send_voip_call(DEVICE, call_id="call_" + "0" * 20, join={})
    _seed_firewall(fw, cfg)
    assert push.send_voip_call(DEVICE, call_id="call_" + "0" * 20, join={}).ok
    lk = LiveKitTransport(cfg, firewall=fw, timeout=0.5)
    with pytest.raises(TransportError):                                     # past the firewall; the (fake) host is unreachable
        lk.list_participants("room_x")
    other = LiveKitTransport(_cfg(livekit_url="wss://unlisted.example.org"), firewall=fw, timeout=0.5)
    with pytest.raises(FirewallBlocked):
        other.list_participants("room_x")


def test_seeding_never_widens_or_removes_what_the_owner_already_set(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    fw.allow("api.example.net", [443], note="owner rule", is_root_owner=True)
    before = fw.list_rules()
    _seed_firewall(fw, _cfg())
    after = fw.list_rules()
    assert all(r in after for r in before) and len(after) == len(before) + 3
