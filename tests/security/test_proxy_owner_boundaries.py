"""Root Owner takeover and owner lockout through the reverse proxy (security batch 1).

In the container topology Caddy shares HOOD's network namespace, so every proxied internet request
reaches HOOD from 127.0.0.1. Before this batch that meant: anyone on the internet could create the
Root Owner before the real owner did, every internet client shared one login rate-limit bucket (a
stranger's wrong passwords locked the owner out), the documented Host setting ``name:443`` refused
every proxied request, and the unauthenticated emergency stop was reachable from the internet.
"""
import http.client
import json
import threading

import pytest

from packages.security.client import classify, host_matches
from services.audit.service import AuditService
from services.auth.auth_service import AuthenticationService
from services.core.emergency_stop import EmergencyStopController
from services.tool_gateway.gateway import ToolGateway
from ui.server import JarvisServer

PASSWORD = "OwnerPassword123!"
PUBLIC = "hood.example.com"


def call(port, path, body=None, headers=None, method=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": f"127.0.0.1:{port}"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    conn.request(method or ("POST" if body is not None else "GET"), path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw
    return resp.status, payload, resp


def via_proxy(client_ip, extra=None):
    """What Caddy sends: the public Host, the client's address and the original scheme."""
    headers = {"Host": PUBLIC, "X-Forwarded-For": client_ip, "X-Forwarded-Proto": "https",
               "X-Forwarded-Host": PUBLIC}
    headers.update(extra or {})
    return headers


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_ALLOWED_HOSTS", PUBLIC + ":443")     # the documented compose setting
    monkeypatch.setenv("HOOD_TRUSTED_PROXY", "1")
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    audit = AuditService(db_path=tmp_path / "audit.db")
    stop = EmergencyStopController(ToolGateway(audit_service=audit, workspace_root=tmp_path), audit)
    srv = JarvisServer(port=0, auth_service=auth, emergency_stop=stop)
    srv.start()
    try:
        yield srv.httpd.server_address[1], auth, stop
    finally:
        srv.stop()


def test_nobody_can_claim_the_root_owner_through_the_proxy(server):
    port, auth, _ = server
    code = auth.setup_code()
    owner = {"username": "zak", "display_name": "Zak", "password": PASSWORD}
    # The takeover: an internet client, through Caddy, even holding the right code.
    status, body, _ = call(port, "/api/auth/init", {**owner, "setup_code": code}, via_proxy("203.0.113.66"))
    assert status == 403 and "locally" in body["error"] and not auth.is_initialized()
    # Locally, but without (or with a wrong) one-time code: refused too.
    assert call(port, "/api/auth/init", owner)[0] == 403
    assert call(port, "/api/auth/init", {**owner, "setup_code": "AAAA-BBBB-CCCC"})[0] == 403
    assert not auth.is_initialized()
    # The owner at the machine, with the code printed in HOOD's window.
    status, body, _ = call(port, "/api/auth/init", {**owner, "setup_code": code.lower()})
    assert status == 200 and body["status"] == "INITIALIZED" and auth.is_initialized()
    assert not auth.setup_code_path().exists() and auth.setup_code() is None
    assert call(port, "/api/auth/init", {**owner, "setup_code": code})[0] == 400        # already done
    status, body, _ = call(port, "/api/auth/status")
    assert body["setup_code_required"] is False


def test_setup_code_guessing_is_rate_limited(server):
    port, auth, _ = server
    owner = {"username": "zak", "password": PASSWORD}
    for _ in range(5):
        assert call(port, "/api/auth/init", {**owner, "setup_code": "WRNG-WRNG-WRNG"})[0] == 403
    assert call(port, "/api/auth/init", {**owner, "setup_code": auth.setup_code()})[0] == 429
    assert not auth.is_initialized()


def test_setup_code_file_is_private_and_stable(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    code = auth.setup_code()
    assert code == auth.setup_code() and len(code) == 14
    import os
    if os.name == "posix":
        assert auth.setup_code_path().stat().st_mode & 0o077 == 0


def test_two_simultaneous_first_runs_create_one_owner(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    results, barrier = [], threading.Barrier(2)

    def claim(name):
        barrier.wait()
        try:
            auth.initialize_root_owner(name, name, PASSWORD)
            results.append(name)
        except Exception:  # noqa: BLE001 - the loser must fail, whatever the error
            pass
    threads = [threading.Thread(target=claim, args=(n,)) for n in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1
    with auth._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users WHERE role='ROOT_OWNER'").fetchone()[0] == 1


def _owner(auth):
    auth.initialize_root_owner("zak", "Zak", PASSWORD)


def test_a_strangers_wrong_passwords_do_not_lock_the_owner_out(server):
    port, auth, _ = server
    _owner(auth)
    attacker, owner_ip = "203.0.113.66", "198.51.100.7"
    for _ in range(5):
        assert call(port, "/api/auth/login", {"username": "zak", "password": "wrong-password-1"},
                    via_proxy(attacker))[0] == 401
    assert call(port, "/api/auth/login", {"username": "zak", "password": PASSWORD}, via_proxy(attacker))[0] == 429
    status, body, resp = call(port, "/api/auth/login", {"username": "zak", "password": PASSWORD}, via_proxy(owner_ip))
    assert status == 200 and body["status"] == "AUTHENTICATED"
    assert "Secure" in resp.getheader("Set-Cookie")          # HTTPS at the proxy: cookie never sent over HTTP


def test_a_client_cannot_choose_its_own_rate_limit_bucket(server):
    port, auth, _ = server
    _owner(auth)
    # Caddy appends the real address last; whatever the client put in front is ignored.
    for fake in ("10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"):
        call(port, "/api/auth/login", {"username": "zak", "password": "wrong-password-1"},
             via_proxy(f"{fake}, 203.0.113.66"))
    assert call(port, "/api/auth/login", {"username": "zak", "password": PASSWORD},
                via_proxy("10.0.0.9, 203.0.113.66"))[0] == 429


def test_recovery_attempts_are_counted_per_real_client(server):
    port, auth, _ = server
    _owner(auth)
    for _ in range(5):
        call(port, "/api/auth/recover", {"recovery_key": "0" * 32, "new_password": "AnotherPassword123!"},
             via_proxy("203.0.113.66"))
    assert call(port, "/api/auth/recover", {"recovery_key": "0" * 32, "new_password": "AnotherPassword123!"},
                via_proxy("203.0.113.66"))[0] == 429
    # The owner, from elsewhere, is not blocked by the attacker's attempts (wrong key -> 400, not 429).
    assert call(port, "/api/auth/recover", {"recovery_key": "1" * 32, "new_password": "AnotherPassword123!"},
                via_proxy("198.51.100.7"))[0] == 400


def test_public_host_works_through_the_proxy_and_others_are_refused(server):
    port, auth, _ = server
    _owner(auth)
    assert call(port, "/api/auth/status", headers=via_proxy("198.51.100.7"))[0] == 200
    login = {"username": "zak", "password": PASSWORD}
    status, _, _ = call(port, "/api/auth/login", login, via_proxy("198.51.100.7", {"Origin": "https://" + PUBLIC}))
    assert status == 200                                      # was 421 / 403 before: owner locked out
    assert call(port, "/api/auth/status", headers={"Host": "evil.example.com"})[0] == 421
    assert call(port, "/api/auth/login", login,
                via_proxy("198.51.100.7", {"Origin": "https://evil.example.com"}))[0] == 403


def test_emergency_stop_needs_a_session_when_it_comes_through_the_proxy(server):
    port, auth, stop = server
    _owner(auth)
    assert call(port, "/api/emergency_stop", {}, via_proxy("203.0.113.66"))[0] == 401
    assert not stop.tool_gateway.stop_latch.engaged
    token = auth.authenticate("zak", PASSWORD, ip_address="198.51.100.7").session_token
    status, _, _ = call(port, "/api/emergency_stop", {},
                        via_proxy("198.51.100.7", {"Authorization": "Bearer " + token}))
    assert status == 200 and stop.tool_gateway.stop_latch.engaged
    stop.reset_stop(authorized_by="zak", is_root_owner=True)
    # On the HOOD machine itself it still works without signing in (safety direction).
    assert call(port, "/api/emergency_stop", {})[0] == 200 and stop.tool_gateway.stop_latch.engaged


def test_forwarded_requests_are_never_local_and_untrusted_proxies_are_not_believed(monkeypatch):
    monkeypatch.delenv("HOOD_TRUSTED_PROXY", raising=False)
    direct = classify("127.0.0.1", {})
    assert direct.direct_local and not direct.forwarded
    assert classify("::ffff:127.0.0.1", {}).direct_local
    spoof = classify("127.0.0.1", {"X-Forwarded-For": "198.51.100.7"})
    assert not spoof.direct_local and spoof.ip == "127.0.0.1"         # not trusted: address not believed
    for header in ("Forwarded", "X-Real-IP", "Via", "X-Forwarded-Host"):
        assert not classify("127.0.0.1", {header: "x"}).direct_local
    monkeypatch.setenv("HOOD_TRUSTED_PROXY", "1")
    info = classify("127.0.0.1", {"X-Forwarded-For": "garbage, 198.51.100.7:51234", "X-Forwarded-Proto": "https"})
    assert info.ip == "198.51.100.7" and info.secure and not info.direct_local
    assert classify("127.0.0.1", {"X-Forwarded-For": "not-an-ip"}).ip == "proxy-unknown"
    assert not classify("203.0.113.5", {}).direct_local                  # never bound, but never local either


def test_host_matching_treats_the_default_port_as_optional():
    assert host_matches(PUBLIC, PUBLIC + ":443") and host_matches(PUBLIC + ":443", PUBLIC)
    assert host_matches("127.0.0.1:8990", "127.0.0.1:8990")
    assert not host_matches(PUBLIC + ":8443", PUBLIC + ":443")
    assert not host_matches("evil.example.com", PUBLIC) and not host_matches("", PUBLIC)
    assert not host_matches(PUBLIC + ".evil.net", PUBLIC)
