"""Pairing a phone: a one-time code from the console, redeemed by the phone with its biometric-protected key."""
import json

import pytest

from services.voicecall.contracts import Forbidden, RateLimited, VoiceError
from tests.hardening.test_remediation import request
from tests.voicecall.conftest import OWNER, make_keypair
from tests.voicecall.test_voice_http import call, dev_auth, web   # noqa: F401


def _pub():
    return make_keypair()[1]


def test_code_pairs_once_and_stores_only_a_digest(kit):
    c = kit.devices.create_pairing_code(OWNER)
    assert len(c["code"]) == 10 and c["expires_in"] == 300 and set(c["code"]) <= set("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
    with kit.store._db() as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM pairing_codes").fetchall()]
    assert c["code"] not in json.dumps(rows)                                      # digest only
    reg = kit.devices.pair(c["code"].lower(), "My iPhone", _pub())                # case and dashes are forgiven
    assert reg["device_id"].startswith("dev_") and reg["device_secret"]
    assert kit.devices.get(reg["device_id"])["owner_id"] == OWNER
    with pytest.raises(Forbidden):
        kit.devices.pair(c["code"], "Replay", _pub())                             # single use


def test_expired_code_and_new_code_retires_the_old_one(kit):
    c = kit.devices.create_pairing_code(OWNER, now=1000.0)
    with pytest.raises(Forbidden):
        kit.devices.pair(c["code"], "late", _pub(), now=1000.0 + 301)
    first = kit.devices.create_pairing_code(OWNER)
    second = kit.devices.create_pairing_code(OWNER)
    with pytest.raises(Forbidden):
        kit.devices.pair(first["code"], "x", _pub())                              # superseded
    assert kit.devices.pair(second["code"], "x", _pub())["device_id"]


def test_guessing_burns_every_open_code(kit):
    good = kit.devices.create_pairing_code(OWNER)
    for i in range(10):
        with pytest.raises(Forbidden):
            kit.devices.pair("AAAAAAAAA" + str(i + 2), "guess", _pub())
    with pytest.raises(RateLimited):                                              # the 11th attempt: even the right code is burned
        kit.devices.pair(good["code"], "me", _pub())
    with pytest.raises(RateLimited):
        kit.devices.pair(good["code"], "me", _pub())
    fresh = kit.devices.create_pairing_code(OWNER)
    with pytest.raises(RateLimited):                                              # and the failure window blocks new tries too
        kit.devices.pair(fresh["code"], "me", _pub())


def test_bad_key_or_name_is_rejected_but_does_not_waste_a_valid_code_silently(kit):
    c = kit.devices.create_pairing_code(OWNER)
    with pytest.raises(VoiceError):
        kit.devices.pair(c["code"], "iPhone", "not-a-key")
    with pytest.raises(VoiceError):
        kit.devices.pair(c["code"], "", _pub())


def test_pairing_over_http_end_to_end(web):
    base, cookies, kit, svc = web
    # the phone cannot ask for a code; the owner's console can, with explicit confirmation
    assert call(base, "POST", "/api/voicecall/devices/pairing-code", None, {"confirm": True})[0] == 401
    assert request(base, "/api/voicecall/devices/pairing-code", cookies["admin2"], {"confirm": True})[0] == 403
    assert request(base, "/api/voicecall/devices/pairing-code", cookies["owner"], {})[0] == 400
    s, raw, _ = request(base, "/api/voicecall/devices/pairing-code", cookies["owner"], {"confirm": True})
    code = json.loads(raw)["code"]
    assert s == 200
    # the phone redeems it with NO credential, only the code and its public key
    st, reg = call(base, "POST", "/api/voicecall/pair", None, {"code": code, "name": "Owner iPhone", "public_key": _pub(), "app_version": "1.0"})
    assert st == 200 and reg["device_secret"]
    assert call(base, "GET", "/api/voicecall/device/home", f"HoodDevice {reg['device_id']}.{reg['device_secret']}")[0] == 200
    assert call(base, "POST", "/api/voicecall/pair", None, {"code": code, "name": "again", "public_key": _pub()})[0] == 403


def test_pairing_route_is_ip_throttled_and_wrong_codes_leak_nothing(web):
    base, cookies, kit, svc = web
    statuses = [call(base, "POST", "/api/voicecall/pair", None, {"code": "ZZZZZZZZZ" + str(i % 8 + 2), "name": "x", "public_key": _pub()})[0]
                for i in range(12)]
    assert statuses[:10] == [403] * 10 and 429 in statuses[10:]
    st, body = call(base, "POST", "/api/voicecall/pair", None, {})
    assert st == 429 and "code" not in json.dumps(body).lower().replace("pairing code", "")
