"""Voice calling through the REAL HTTP server and REAL authentication/RBAC.
SIMULATED: transport (in-memory), push (FakePush), model (scripted), engine (FakeEngine)."""
import base64
import json
import urllib.error
import urllib.request

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from packages.contracts import RiskLevel
from services.auth.auth_service import AuthenticationService, UserRole
from services.voicecall.confirm import signing_string
from services.voicecall.policy import CallPermissionManager
from services.voicecall.service import VoiceCallService
from ui import routes as feature_routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request
from tests.voicecall.conftest import make_keypair


@pytest.fixture
def web(kit, tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "admin2", "Second Admin", "AdminPassword123!", UserRole.ADMINISTRATOR)
    assert root["user_id"] == "user_root_owner_01"
    perms = CallPermissionManager(kit.store, kit.devices, auth, clock=kit.clock)
    kit.sessions.perms = perms
    server = JarvisServer(port=0, auth_service=auth, interaction_service=kit.interaction)
    svc = VoiceCallService(cfg=kit.cfg, store=kit.store, devices=kit.devices, transport=kit.transport, cost=kit.cost,
                           perms=perms, events=kit.events, confirm=kit.confirm, bridge=kit.bridge,
                           sessions=kit.sessions, push=kit.push, policy=kit.policy, dispatcher=kit.dispatcher)
    feature_routes.SERVICES["voicecall"] = svc
    server.start()
    base = "http://127.0.0.1:" + str(server.httpd.server_address[1])
    cookies = {n: "hood_session=" + auth.authenticate(n, p).session_token
               for n, p in (("owner", "OwnerPassword123!"), ("admin2", "AdminPassword123!"))}
    try:
        yield base, cookies, kit, svc
    finally:
        server.stop()
        feature_routes.SERVICES.pop("voicecall", None)


def call(base, method, path, auth=None, data=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if auth:
        h["Authorization"] = auth
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, (json.loads(body) if body else {})


def dev_auth(kit):
    return f"HoodDevice {kit.device_id}.{kit.device_secret}"


def agent_auth(kit, cid):
    return "Bearer " + kit.sessions.agent_token(kit.store.get_call(cid))


def test_credentials_are_separated_and_required(web):
    base, cookies, kit, svc = web
    assert call(base, "GET", "/api/voicecall/device/home")[0] == 401                         # nothing
    assert call(base, "GET", "/api/voicecall/agent/session")[0] == 401
    assert request(base, "/api/voicecall/status")[0] == 401                                  # owner route without session
    # a valid Hood *session* is not a device or agent credential
    s, _, _ = request(base, "/api/voicecall/device/home", cookies["owner"])
    assert s == 401
    assert call(base, "GET", "/api/voicecall/device/home", headers={"Cookie": cookies["owner"]})[0] == 401
    # a device credential does not open the general API, and an agent token opens nothing but its own call
    assert call(base, "GET", "/api/agents/missions", dev_auth(kit))[0] == 401
    assert call(base, "POST", "/api/chat", dev_auth(kit), {"text": "hi"})[0] in (401, 403)
    assert call(base, "GET", "/api/voicecall/device/home", "Bearer " + kit.sessions.agent_token(
        kit.store.get_call(kit.sessions.start_owner_call("user_root_owner_01", kit.device)["call"]["call_id"])))[0] == 401
    assert call(base, "GET", "/api/voicecall/device/home", dev_auth(kit))[0] == 200


def test_owner_only_for_console_routes_and_device_registration(web):
    base, cookies, kit, svc = web
    assert request(base, "/api/voicecall/status", cookies["owner"])[0] == 200
    assert request(base, "/api/voicecall/status", cookies["admin2"])[0] == 403               # an administrator is not the owner
    _, new_pub = make_keypair()
    body = {"name": "New iPhone", "public_key": new_pub}
    assert request(base, "/api/voicecall/devices/register", cookies["owner"], body)[0] == 400   # needs explicit confirm
    assert request(base, "/api/voicecall/devices/register", cookies["admin2"], {**body, "confirm": True})[0] == 403
    s, raw, _ = request(base, "/api/voicecall/devices/register", cookies["owner"], {**body, "confirm": True})
    reg = json.loads(raw)
    assert s == 200 and reg["device_secret"] and reg["device_id"].startswith("dev_")
    listing = json.loads(request(base, "/api/voicecall/devices", cookies["owner"])[1])
    assert reg["device_secret"] not in json.dumps(listing)                                  # never listed again


def test_full_owner_call_over_http(web):
    base, cookies, kit, svc = web
    st, started = call(base, "POST", "/api/voicecall/calls/start", dev_auth(kit), {"language": "fr"})
    assert st == 200 and started["call"]["language"] == "fr" and started["join"]["identity"] == kit.device_id
    cid = started["call"]["call_id"]
    # the worker (agent credential) reads the session and takes a turn through the one Hood
    ag = agent_auth(kit, cid)
    st, sess = call(base, "GET", "/api/voicecall/agent/session", ag)
    assert st == 200 and sess["call_id"] == cid and sess["language"] == "fr" and sess["mode"] == "realtime"
    kit.router.text = "The coffee shop mission is waiting for your plan approval."
    kit.transport.join(cid, kit.device_id)
    assert call(base, "POST", "/api/voicecall/agent/heartbeat", ag, {"usage": {"audio_in_s": 3, "audio_out_s": 2}})[1]["continue"] is True
    st, turn = call(base, "POST", "/api/voicecall/agent/turn", ag, {"text": "Where are we with the coffee shop?"})
    assert st == 200 and "plan approval" in turn["speech"] and not turn["end_call"]
    assert "LIVE PHONE CALL" in kit.router.requests[-1].prompt
    st, conn = call(base, "POST", f"/api/voicecall/calls/{cid}/connected", dev_auth(kit))
    assert st == 200 and conn["state"] == "ACTIVE"
    st, g = call(base, "GET", f"/api/voicecall/calls/{cid}", dev_auth(kit))
    assert g["call"]["turns"] == 1 and any(e["kind"] == "turn" for e in g["events"])
    st, bye = call(base, "POST", "/api/voicecall/agent/turn", ag, {"text": "goodbye"})
    assert bye["end_call"] is True
    st, ended = call(base, "POST", f"/api/voicecall/calls/{cid}/end", dev_auth(kit))
    assert ended["call"]["state"] == "ENDED" and cid not in kit.transport.rooms
    # after the call ends the worker's credential is useless
    assert call(base, "POST", "/api/voicecall/agent/turn", ag, {"text": "hello?"})[0] == 403
    # ... for every route that reads the briefing or spends on speech, not just turns (a leaked token is worthless)
    for method, path, body in (("GET", "/api/voicecall/agent/session", None),
                               ("POST", "/api/voicecall/agent/stt", {"audio_b64": "AAAA", "mime_type": "audio/wav"}),
                               ("POST", "/api/voicecall/agent/tts", {"text": "hello"})):
        assert call(base, method, path, ag, body)[0] == 403, path


def test_spoken_approval_over_http_changes_nothing_and_signed_confirmation_does(web):
    base, cookies, kit, svc = web
    req = kit.approvals.create_request(task_id="t", action_type="task_execution", target="backup-1", reason="space",
                                       risk_level=RiskLevel.L3, recommended_option="approve")
    cid = call(base, "POST", "/api/voicecall/calls/start", dev_auth(kit), {})[1]["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    ag = agent_auth(kit, cid)
    call(base, "POST", "/api/voicecall/agent/heartbeat", ag, {})
    st, turn = call(base, "POST", "/api/voicecall/agent/turn", ag, {"text": f"approve {req.approval_id}"})
    assert st == 200 and "can't approve" in turn["speech"].lower()
    assert kit.approvals.get_request(req.approval_id).status.value == "PENDING"
    # the phone lists it, asks for a challenge, signs with its key (Face ID on a real device), submits
    items = call(base, "GET", "/api/voicecall/confirmations", dev_auth(kit))[1]["items"]
    assert [i["subject"] for i in items if i["kind"] == "approval"] == [req.approval_id]
    st, ch = call(base, "POST", "/api/voicecall/confirmations/challenge", dev_auth(kit),
                  {"kind": "approval", "subject": req.approval_id, "decision": "approve"})
    assert st == 200 and ch["signing_string"].startswith("hood-confirm-v1\n")
    sig = base64.b64encode(kit.key.sign(signing_string(ch).encode(), ec.ECDSA(hashes.SHA256()))).decode()
    assert call(base, "POST", "/api/voicecall/confirmations/submit", dev_auth(kit),
                {"challenge_id": ch["challenge_id"], "signature": "AAAA"})[0] == 403          # bad signature
    st, res = call(base, "POST", "/api/voicecall/confirmations/submit", dev_auth(kit),
                   {"challenge_id": ch["challenge_id"], "signature": sig})
    assert st == 200 and res["applied"] and kit.approvals.get_request(req.approval_id).status.value == "APPROVED"
    assert call(base, "POST", "/api/voicecall/confirmations/submit", dev_auth(kit),
                {"challenge_id": ch["challenge_id"], "signature": sig})[0] == 409                  # replay


def test_bad_device_credentials_are_throttled_and_never_leak_which_part_was_wrong(web):
    base, cookies, kit, svc = web
    outcomes = [call(base, "GET", "/api/voicecall/device/home", f"HoodDevice {kit.device_id}.bad{i}")[0] for i in range(12)]
    assert outcomes[:10] == [401] * 10 and 429 in outcomes[10:]
    st, body = call(base, "GET", "/api/voicecall/device/home", "HoodDevice dev_unknown.secret")
    assert st in (401, 429) and "device_id" not in json.dumps(body)


def test_revoking_a_device_over_http_ends_its_call_and_blocks_it(web):
    base, cookies, kit, svc = web
    cid = call(base, "POST", "/api/voicecall/calls/start", dev_auth(kit), {})[1]["call"]["call_id"]
    assert request(base, "/api/voicecall/devices/revoke", cookies["owner"], {"device_id": kit.device_id})[0] == 400
    assert request(base, "/api/voicecall/devices/revoke", cookies["owner"], {"device_id": kit.device_id, "confirm": True})[0] == 200
    assert kit.store.get_call(cid).state.terminal
    assert call(base, "GET", "/api/voicecall/device/home", dev_auth(kit))[0] in (401, 429)


def test_host_header_allowlist_and_origin_checks_still_apply(web):
    base, cookies, kit, svc = web
    st, _ = call(base, "GET", "/api/voicecall/device/home", dev_auth(kit), headers={"Host": "evil.example"})
    assert st == 421
    st, _ = call(base, "POST", "/api/voicecall/calls/start", dev_auth(kit), {}, headers={"Origin": "http://evil.example"})
    assert st == 403


def test_settings_and_costs_over_http(web):
    base, cookies, kit, svc = web
    st, s = call(base, "POST", "/api/voicecall/device/settings", dev_auth(kit),
                 {"settings": {"language": "fr", "quiet_start": "21:30", "daily_usd": 1.0}})
    assert st == 200 and s["language"] == "fr" and s["quiet_start"] == "21:30" and s["daily_usd"] == 1.0
    st, _ = call(base, "POST", "/api/voicecall/device/settings", dev_auth(kit), {"settings": {"daily_usd": 999}})
    assert st == 400                                                                          # cannot raise the server limit
    st, _ = call(base, "POST", "/api/voicecall/device/settings", dev_auth(kit), {"settings": {"unknown": 1}})
    assert st == 400
    costs = json.loads(request(base, "/api/voicecall/costs", cookies["owner"])[1])
    assert set(costs["scenarios_usd"]) >= {"gemini-live"} and costs["scenarios_usd"]["gemini-live"][0]["minutes"] == 100
    status = json.loads(request(base, "/api/voicecall/status", cookies["owner"])[1])
    assert status["provider"]["id"] == "gemini-live" and "api_secret" not in json.dumps(status).lower()
    assert "S" * 40 not in json.dumps(status)


def test_test_event_rings_the_phone_and_needs_confirmation(web):
    base, cookies, kit, svc = web
    body = {"event_type": "decision_blocking", "severity": "high", "summary": "Test: a decision is blocking a mission"}
    assert request(base, "/api/voicecall/test-event", cookies["owner"], body)[0] == 400
    st, raw, _ = request(base, "/api/voicecall/test-event", cookies["owner"], {**body, "confirm": True})
    out = json.loads(raw)
    assert st == 200 and out["decision"] == "call" and kit.push.voip[0]["call_id"] == out["call_id"]
    cid = out["call_id"]
    st, ans = call(base, "POST", f"/api/voicecall/calls/{cid}/answer", dev_auth(kit))
    assert st == 200 and ans["join"]["identity"] == kit.device_id
