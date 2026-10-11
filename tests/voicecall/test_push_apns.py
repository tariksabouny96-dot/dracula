"""APNs client: provider token, headers, payload limits, dead-token handling, and a real HTTP/2 round trip
against a LOCAL server (verifies our request shape; it is NOT Apple and proves nothing about delivery)."""
import json
import socket
import threading

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from services.voicecall.config import VoiceCallConfig
from services.voicecall.contracts import VoiceError
from services.voicecall.push import (MAX_VOIP_PAYLOAD, HttpxApnsTransport, PushNotificationService, SANDBOX_HOST, PROD_HOST)
from services.voicecall.tokens import b64u_decode, verify_p256
from tests.voicecall.conftest import FakeClock

KEY = ec.generate_private_key(ec.SECP256R1())
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
DEVICE = {"device_id": "dev_1", "voip_token": "ab" * 32, "alert_token": "cd" * 32, "apns_env": "sandbox"}


class RecordingTransport:
    def __init__(self, status=200, body=b"", headers=None):
        self.calls, self.status, self.body, self.headers = [], status, body, headers or {"apns-id": "ID-1"}

    def post(self, host, path, headers, body):
        self.calls.append({"host": host, "path": path, "headers": headers, "body": body})
        return self.status, self.headers, self.body


def svc(transport, clock=None, **cfg):
    c = VoiceCallConfig(apns_team_id="TEAM123456", apns_key_id="KEY1234567", apns_bundle_id="com.example.hood", **cfg)
    return PushNotificationService(c, transport=transport, clock=clock or FakeClock(), key_pem=PEM)


def test_voip_push_shape():
    t = RecordingTransport()
    s = svc(t)
    r = s.send_voip_call(DEVICE, call_id="call_abc", join={"url": "wss://x", "token": "t"}, reason="Disk is full")
    assert r.ok and r.apns_id == "ID-1"
    c = t.calls[0]
    assert c["host"] == SANDBOX_HOST and c["path"] == "/3/device/" + "ab" * 32
    h = c["headers"]
    assert h["apns-push-type"] == "voip" and h["apns-topic"] == "com.example.hood.voip" and h["apns-priority"] == "10"
    assert h["apns-collapse-id"] == "call_abc" and int(h["apns-expiration"]) > 0         # stale rings are discarded
    body = json.loads(c["body"])
    assert body["type"] == "hood.incoming_call" and body["join"]["token"] == "t" and body["call_id"] == "call_abc"
    assert "alert" not in body["aps"]                                                    # a ring is not a visible banner


def test_alert_push_is_a_different_type_and_topic():
    t = RecordingTransport()
    svc(t).send_alert(DEVICE, title="Missed call from Hood", body="Disk is full", time_sensitive=True,
                      data={"action": "callback"})
    h = t.calls[0]["headers"]
    assert h["apns-push-type"] == "alert" and h["apns-topic"] == "com.example.hood"      # NOT the .voip topic
    body = json.loads(t.calls[0]["body"])
    assert body["aps"]["alert"]["title"] == "Missed call from Hood" and body["aps"]["interruption-level"] == "time-sensitive"
    assert t.calls[0]["path"].endswith("cd" * 32)                                        # alert token, not the VoIP token


def test_production_environment_uses_the_production_host():
    t = RecordingTransport()
    svc(t).send_voip_call({**DEVICE, "apns_env": "production"}, call_id="c", join={})
    assert t.calls[0]["host"] == PROD_HOST


def test_provider_token_is_es256_cached_and_refreshed_inside_apples_window():
    t = RecordingTransport()
    clock = FakeClock()
    s = svc(t, clock)
    s.send_alert(DEVICE, title="a", body="b")
    first = t.calls[0]["headers"]["authorization"]
    assert first.startswith("bearer ")
    jwt = first[7:]
    head, body, sig = jwt.split(".")
    assert json.loads(b64u_decode(head)) == {"alg": "ES256", "kid": "KEY1234567"}
    claims = json.loads(b64u_decode(body))
    assert claims["iss"] == "TEAM123456" and claims["iat"] == int(clock())
    assert verify_p256(KEY.public_key(), f"{head}.{body}".encode(), b64u_decode(sig))
    clock.advance(20 * 60)
    s.send_alert(DEVICE, title="a", body="b")
    assert t.calls[1]["headers"]["authorization"] == first                               # not refreshed < 20 min (Apple rejects)
    clock.advance(25 * 60)                                                               # 45 min total: refreshed before 60
    s.send_alert(DEVICE, title="a", body="b")
    assert t.calls[2]["headers"]["authorization"] != first


@pytest.mark.parametrize("status,reason,dead", [(410, "Unregistered", True), (400, "BadDeviceToken", True),
                                                (400, "DeviceTokenNotForTopic", True), (429, "TooManyRequests", False),
                                                (403, "InvalidProviderToken", False)])
def test_error_mapping_and_dead_tokens(status, reason, dead):
    r = svc(RecordingTransport(status, json.dumps({"reason": reason}).encode())).send_voip_call(DEVICE, call_id="c", join={})
    assert not r.ok and r.status == status and r.reason == reason and r.dead_token is dead


def test_oversized_payload_is_refused_before_sending():
    t = RecordingTransport()
    with pytest.raises(VoiceError):
        svc(t).send_voip_call(DEVICE, call_id="c", join={"token": "x" * (MAX_VOIP_PAYLOAD + 10)})
    assert t.calls == []


def test_transport_failure_is_reported_not_raised():
    class Boom:
        def post(self, *a):
            raise OSError("network down")
    r = svc(Boom()).send_voip_call(DEVICE, call_id="c", join={})
    assert not r.ok and "OSError" in r.reason


def test_unconfigured_service_does_not_pretend():
    s = PushNotificationService(VoiceCallConfig(), transport=RecordingTransport())
    r = s.send_voip_call(DEVICE, call_id="c", join={})
    assert not r.ok and "not configured" in r.reason


def test_egress_firewall_is_consulted():
    class Fw:
        def __init__(self):
            self.seen = []

        def enforce(self, host, port, purpose=""):
            self.seen.append((host, port, purpose))
            raise PermissionError("denied")
    fw = Fw()
    s = PushNotificationService(VoiceCallConfig(apns_team_id="T", apns_key_id="K", apns_bundle_id="b"),
                                transport=RecordingTransport(), firewall=fw, key_pem=PEM)
    with pytest.raises(PermissionError):
        s.send_voip_call(DEVICE, call_id="c", join={})
    assert fw.seen == [(SANDBOX_HOST, 443, "voicecall:apns")]


# --------------------------------------------------------------------------- real HTTP/2 round trip (local server)
def _h2_server(response_status=200, response_body=b""):
    import h2.config
    import h2.connection
    import h2.events
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    seen = []

    def run():
        conn_sock, _ = sock.accept()
        conn = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
        conn.initiate_connection()
        conn_sock.sendall(conn.data_to_send())
        bodies, heads = {}, {}
        conn_sock.settimeout(5)
        try:
            while len(seen) < 1:
                data = conn_sock.recv(65535)
                if not data:
                    break
                for ev in conn.receive_data(data):
                    if isinstance(ev, h2.events.RequestReceived):
                        heads[ev.stream_id] = {k.decode(): v.decode() for k, v in ev.headers}
                        bodies[ev.stream_id] = b""
                    elif isinstance(ev, h2.events.DataReceived):
                        bodies[ev.stream_id] += ev.data
                        conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                    elif isinstance(ev, h2.events.StreamEnded):
                        seen.append((heads[ev.stream_id], bodies[ev.stream_id]))
                        conn.send_headers(ev.stream_id, [(":status", str(response_status)), ("apns-id", "LOCAL-1"),
                                                         ("content-type", "application/json")])
                        conn.send_data(ev.stream_id, response_body, end_stream=True)
                conn_sock.sendall(conn.data_to_send())
        except (socket.timeout, OSError):
            pass
        finally:
            conn_sock.close()
            sock.close()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    return sock.getsockname()[1], seen, th


def test_real_http2_request_has_exactly_the_headers_apns_requires():
    port, seen, th = _h2_server()
    transport = HttpxApnsTransport(base_url=f"http://127.0.0.1:{port}")
    s = svc(transport)
    r = s.send_voip_call(DEVICE, call_id="call_http2", join={"token": "T"}, reason="Test")
    th.join(5)
    assert r.ok and r.apns_id == "LOCAL-1"
    heads, body = seen[0]
    assert heads[":method"] == "POST" and heads[":path"] == "/3/device/" + "ab" * 32
    assert heads["apns-push-type"] == "voip" and heads["apns-topic"] == "com.example.hood.voip"
    assert heads["apns-priority"] == "10" and heads["authorization"].startswith("bearer ")
    assert json.loads(body)["call_id"] == "call_http2"


def test_real_http2_error_body_is_parsed():
    port, seen, th = _h2_server(410, json.dumps({"reason": "Unregistered"}).encode())
    s = svc(HttpxApnsTransport(base_url=f"http://127.0.0.1:{port}"))
    r = s.send_alert(DEVICE, title="t", body="b")
    th.join(5)
    assert not r.ok and r.status == 410 and r.dead_token
