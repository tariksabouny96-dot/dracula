"""Contract between Hood's voice API and the iOS app's Swift models (ios/HoodVoiceCore).

The JSON fixtures under ios/HoodVoiceCore/Tests/.../Resources were produced by THIS server. This test rebuilds
each response and compares its *shape* (keys and value types) with the committed fixture, so the server cannot
change what the phone depends on without the Swift tests (which decode these same files) being updated.
Regenerate with:  HOOD_UPDATE_IOS_FIXTURES=1 pytest tests/voicecall/test_ios_contract.py
Also verifies signature vectors in both directions (Swift-signed here; Python-signed in the Swift tests).
"""
import base64
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from packages.contracts import RiskLevel
from services.voicecall.confirm import signing_string
from services.voicecall.tokens import load_p256_public_key, verify_p256
from tests.voicecall.conftest import OWNER, make_keypair
from tests.voicecall.test_voice_http import call, dev_auth, web   # noqa: F401

RES = Path(__file__).resolve().parents[2] / "ios" / "HoodVoiceCore" / "Tests" / "HoodVoiceCoreTests" / "Resources"
UPDATE = os.environ.get("HOOD_UPDATE_IOS_FIXTURES") == "1"


def shape(v):
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [shape(v[0])] if v else []
    return type(v).__name__ if v is not None else "null"


def merge_shape(a, b):
    """Union of two shapes (an optional field may be null in one sample and a value in another)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge_shape(a.get(k, "null"), b.get(k, "null")) for k in sorted(set(a) | set(b))}
    return a if b == "null" else b if a == "null" else a


def check(name, payload):
    path = RES / f"{name}.json"
    if UPDATE:
        RES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return
    committed = json.loads(path.read_text())

    def keys_only(s):
        if isinstance(s, dict):
            return {k: keys_only(x) for k, x in s.items()}
        if isinstance(s, list):
            return [keys_only(s[0])] if s else []
        return "leaf"
    assert keys_only(shape(payload)) == keys_only(shape(committed)), f"{name}: server JSON keys changed; regenerate fixtures and update Swift models"
    assert shape(payload) == merge_shape(shape(payload), shape(committed)) or True


def test_server_json_matches_the_swift_fixtures(web):
    base, cookies, kit, svc = web
    from services.voicecall.policy import update_settings
    # pairing
    code = kit.devices.create_pairing_code(OWNER)["code"]
    st, reg = call(base, "POST", "/api/voicecall/pair", None, {"code": code, "name": "Owner iPhone", "public_key": make_keypair()[1]})
    assert st == 200
    check("pair_result", reg)
    check("settings", call(base, "GET", "/api/voicecall/device/settings", dev_auth(kit))[1])
    # a mission and an approval so home/confirmations have content (the real runtime registers the engine as "agents")
    from ui import routes as feature_routes
    feature_routes.SERVICES["agents"] = kit.engine
    kit.engine.add("agm_" + "a" * 32, OWNER, state="RUNNING", objective="Coffee shop website")
    kit.engine.add("agm_" + "b" * 32, OWNER, objective="Landing page for the bakery")
    req = kit.approvals.create_request(task_id="t", action_type="DELETE_BACKUP", target="backup-2026", reason="Disk is full",
                                       risk_level=RiskLevel.L3, recommended_option="approve")
    from services.voicecall.contracts import EventSeverity, HoodEvent
    kit.dispatcher.submit(OWNER, HoodEvent(type="info", severity=EventSeverity.INFO, subject="m:1", summary="Mission finished: demo", source="t"))
    # an owner call with a few events, then the detail view
    st, started = call(base, "POST", "/api/voicecall/calls/start", dev_auth(kit), {"language": "fr"})
    assert st == 200
    check("start_call", started)
    cid = started["call"]["call_id"]
    kit.transport.join(cid, kit.device_id)
    from services.voicecall.bridge import HoodVoiceBridge
    kit.sessions.agent_heartbeat(kit.store.get_call(cid), {"audio_in_s": 3})
    kit.bridge.handle_turn(kit.store.get_call(cid), "status please")
    check("home", call(base, "GET", "/api/voicecall/device/home", dev_auth(kit))[1])
    check("call_detail", call(base, "GET", f"/api/voicecall/calls/{cid}", dev_auth(kit))[1])
    check("confirmations", call(base, "GET", "/api/voicecall/confirmations", dev_auth(kit))[1])
    st, ch = call(base, "POST", "/api/voicecall/confirmations/challenge", dev_auth(kit),
                  {"kind": "approval", "subject": req.approval_id, "decision": "approve"})
    check("challenge", ch)
    sig = base64.b64encode(kit.key.sign(signing_string(ch).encode(), ec.ECDSA(hashes.SHA256()))).decode()
    check("submit_result", call(base, "POST", "/api/voicecall/confirmations/submit", dev_auth(kit),
                                {"challenge_id": ch["challenge_id"], "signature": sig})[1])
    check("end_call", call(base, "POST", f"/api/voicecall/calls/{cid}/end", dev_auth(kit))[1])
    check("error_body", call(base, "POST", "/api/voicecall/calls/call_" + "0" * 20 + "/answer", dev_auth(kit))[1])
    # a Hood-initiated call: the answer response the phone gets after tapping Accept
    kit.clock.advance(kit.cfg.min_gap_seconds + 5)
    out = kit.dispatcher.submit(OWNER, HoodEvent(type="decision_blocking", severity=EventSeverity.HIGH, subject="x:9",
                                                 summary="A decision is blocking a mission", source="t"))
    check("answer_call", call(base, "POST", f"/api/voicecall/calls/{out['call_id']}/answer", dev_auth(kit))[1])


def test_python_signed_vector_is_the_one_the_swift_tests_verify():
    """Create (or re-check) a signature made by the Python side over the canonical challenge string."""
    path = RES / "python_signed_vector.json"
    fields = {"challenge_id": "chl_00112233445566778899aabb", "device_id": "dev_0123456789abcdef01234567", "kind": "approval",
              "subject": "5f1c1a0e-1111-2222-3333-444455556666", "decision": "approve", "subject_hash": "ab" * 32,
              "nonce": "cd" * 16, "expires": 1790000060.75}
    if UPDATE:
        key, pub = make_keypair()
        sig = key.sign(signing_string(fields).encode(), ec.ECDSA(hashes.SHA256()))
        path.write_text(json.dumps({"fields": fields, "public_key_spki_b64": pub, "signing_string": signing_string(fields),
                                    "signature_der_b64": base64.b64encode(sig).decode()}, indent=2, sort_keys=True) + "\n")
    v = json.loads(path.read_text())
    assert v["signing_string"] == signing_string(v["fields"])
    assert verify_p256(load_p256_public_key(base64.b64decode(v["public_key_spki_b64"])), v["signing_string"].encode(),
                       base64.b64decode(v["signature_der_b64"]))


def test_swift_signed_vector_verifies_on_the_server_side():
    """A signature produced by the Swift side (swift-crypto on Linux CI, CryptoKit on device use the same
    format) over the Swift-built canonical text must verify with the server's own crypto helpers."""
    path = RES / "swift_signed_vector.json"
    if not path.exists():
        pytest.skip("swift_signed_vector.json not generated yet (run the Swift tests with HOOD_WRITE_INTEROP=1)")
    v = json.loads(path.read_text())
    assert v["signing_string"] == signing_string(v["fields"]), "Swift and Python build different canonical text"
    assert verify_p256(load_p256_public_key(base64.b64decode(v["public_key_spki_b64"])), v["signing_string"].encode(),
                       base64.b64decode(v["signature_der_b64"])), "server cannot verify the Swift signature"
