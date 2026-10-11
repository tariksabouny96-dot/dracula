"""Operator commands (offline): configuration doctor, pairing codes, device list/revoke."""
import datetime as dt
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from services.voicecall import cli
from services.voicecall.config import Secret, VoiceCallConfig
from tests.voicecall.conftest import OWNER, make_keypair

ROOT = Path(__file__).resolve().parents[2]
LIVEKIT_SECRET = "s" * 40


def _cfg(tmp_path, **kw):
    base = dict(enabled=True, livekit_url="wss://hood.livekit.cloud", livekit_api_key="APIkey123",
                livekit_api_secret=Secret(LIVEKIT_SECRET), agent_secret=Secret("a" * 48))
    base.update(kw)
    return VoiceCallConfig(**base)


def _levels(checks):
    return {c.name: c.level for c in checks}


def _p8(path, key=None, mode=0o600):
    key = key or ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(path, mode)
    return str(path)


def test_an_unconfigured_install_says_exactly_what_is_missing():
    lv = _levels(cli.doctor(VoiceCallConfig()))
    assert lv["livekit.url"] == lv["livekit.key"] == lv["livekit.secret"] == cli.FAIL
    assert lv["enabled"] == cli.WARN and lv["apns"] == cli.WARN               # off, and Hood cannot ring the phone yet


def test_a_complete_configuration_has_no_failures(tmp_path):
    cfg = _cfg(tmp_path, apns_team_id="TEAM123456", apns_key_id="KEY1234567", apns_key_path=_p8(tmp_path / "k.p8"),
               apns_bundle_id="com.example.hoodvoice")
    checks = cli.doctor(cfg)
    assert cli.exit_code(checks) == 0, cli.format_checks(checks)
    assert "com.example.hoodvoice.voip" in cli.format_checks(checks)


def test_cleartext_livekit_is_refused_except_on_loopback(tmp_path):
    assert _levels(cli.doctor(_cfg(tmp_path, livekit_url="ws://example.com:7880")))["livekit.url"] == cli.FAIL
    assert _levels(cli.doctor(_cfg(tmp_path, livekit_url="ws://127.0.0.1:7880")))["livekit.url"] == cli.WARN
    assert _levels(cli.doctor(_cfg(tmp_path, livekit_url="wss://x.livekit.cloud")))["livekit.url"] == cli.OK


def test_apns_key_must_be_an_ec_p256_pem_and_not_world_readable(tmp_path):
    common = dict(apns_team_id="TEAM123456", apns_key_id="KEY1234567", apns_bundle_id="com.example.hoodvoice")
    lv = _levels(cli.doctor(_cfg(tmp_path, apns_key_path=_p8(tmp_path / "wide.p8", mode=0o644), **common)))
    assert lv["apns.key"] == cli.WARN
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    lv = _levels(cli.doctor(_cfg(tmp_path, apns_key_path=_p8(tmp_path / "rsa.p8", key=rsa_key), **common)))
    assert lv["apns.key.type"] == cli.FAIL
    junk = tmp_path / "junk.p8"
    junk.write_text("not a key")
    os.chmod(junk, 0o600)
    assert _levels(cli.doctor(_cfg(tmp_path, apns_key_path=str(junk), **common)))["apns.key.type"] == cli.FAIL
    assert _levels(cli.doctor(_cfg(tmp_path, apns_key_path=str(tmp_path / "missing.p8"), **common)))["apns.key"] == cli.FAIL
    bad_bundle = _levels(cli.doctor(_cfg(tmp_path, apns_key_path=_p8(tmp_path / "ok.p8"), **{**common, "apns_bundle_id": "nodots"})))
    assert bad_bundle["apns.bundle"] == cli.FAIL


def test_stale_prices_and_unknown_provider_are_called_out(tmp_path):
    far = dt.date.today() + dt.timedelta(days=400)
    assert any(c.name == "provider.prices" and c.level == cli.WARN for c in cli.doctor(_cfg(tmp_path), today=far))
    assert _levels(cli.doctor(_cfg(tmp_path, provider="nonsense")))["provider"] == cli.FAIL


def test_short_agent_secret_fails_and_no_secret_value_is_ever_printed(tmp_path):
    checks = cli.doctor(_cfg(tmp_path, agent_secret=Secret("short")))
    assert _levels(checks)["agent.secret"] == cli.FAIL
    text = cli.format_checks(cli.doctor(_cfg(tmp_path, apns_team_id="TEAM123456", apns_key_id="KEY1234567",
                                             apns_key_path=_p8(tmp_path / "k.p8"), apns_bundle_id="com.example.hoodvoice")))
    for secret in (LIVEKIT_SECRET, "a" * 48, "APIkey123"):
        assert secret not in text


def test_a_pairing_code_from_the_cli_pairs_a_phone_once(tmp_path):
    from services.voicecall.devices import DeviceRegistry
    from services.voicecall.store import VoiceStore
    code = cli.create_pairing_code(tmp_path)["code"]
    reg = DeviceRegistry(VoiceStore(tmp_path))
    out = reg.pair(code, "Owner iPhone", make_keypair()[1])
    assert out["device_id"].startswith("dev_") and out["device_secret"]
    with pytest.raises(Exception):
        reg.pair(code, "Second", make_keypair()[1])                      # single use
    rows = cli.list_devices(tmp_path)
    assert [d["name"] for d in rows] == ["Owner iPhone"] and rows[0]["revoked"] is False
    assert cli.revoke_device(tmp_path, out["device_id"]) is True
    assert cli.revoke_device(tmp_path, out["device_id"]) is False        # already revoked
    assert cli.list_devices(tmp_path)[0]["revoked"] is True


def test_hood_cli_wires_the_commands_without_starting_the_runtime(tmp_path):
    env = {**os.environ, "HOOD_DATA_DIR": str(tmp_path), "HOOD_ENV_FILE": str(tmp_path / "none.env")}
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "hood_cli.py"), "voicecall", *a], capture_output=True,
                                    text=True, env=env, timeout=120, cwd=ROOT)
    pair = run("pair")
    assert pair.returncode == 0 and "Pairing code:" in pair.stdout
    assert "No paired phones." in run("devices").stdout
    assert run("doctor").returncode == 1                                  # nothing configured: FAIL lines => non-zero
    assert run("revoke", "dev_" + "0" * 24).returncode == 1
