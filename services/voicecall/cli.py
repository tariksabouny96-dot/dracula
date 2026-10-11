"""Operator commands for voice calling: ``python hood_cli.py voicecall doctor|pair|devices|revoke``.

Everything here is local and offline: it reads configuration and the voice database in HOOD_DATA_DIR. It never
contacts LiveKit, Apple or a model provider, never spends money, and never prints a secret value.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from .config import VoiceCallConfig
from .devices import DeviceRegistry
from .profiles import PROFILES, get_profile
from .store import VoiceStore

ROOT_OWNER_ID = "user_root_owner_01"
OK, WARN, FAIL, INFO = "ok", "WARN", "FAIL", "info"
_BUNDLE_RE = r"^[A-Za-z0-9]+(\.[A-Za-z0-9-]+)+$"


@dataclass
class Check:
    level: str
    name: str
    message: str


def _is_loopback(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")


def _env_present(name: str) -> bool:
    f = os.environ.get(name + "_FILE", "").strip()
    if f:
        return Path(f).is_file()
    return bool(os.environ.get(name, "").strip())


def doctor(cfg: VoiceCallConfig, *, today: Optional[_dt.date] = None,
           module_available: Callable[[str], bool] = lambda m: importlib.util.find_spec(m) is not None) -> List[Check]:
    """Offline configuration checks. FAIL = calls cannot work or are unsafe; WARN = works but review it."""
    import re
    out: List[Check] = []
    add = lambda level, name, msg: out.append(Check(level, name, msg))
    today = today or _dt.date.today()

    add(OK if cfg.enabled else WARN, "enabled",
        "voice calling is ON" if cfg.enabled else "voice calling is OFF (set HOOD_VOICECALL_ENABLED=1); nothing listens or connects while off")

    # ---- LiveKit
    if not cfg.livekit_url:
        add(FAIL, "livekit.url", "LIVEKIT_URL is not set (wss://<project>.livekit.cloud, or your own server)")
    else:
        u = urlparse(cfg.livekit_url)
        if u.scheme == "wss":
            add(OK, "livekit.url", f"{u.scheme}://{u.hostname}")
        elif u.scheme == "ws" and _is_loopback(u.hostname or ""):
            add(WARN, "livekit.url", "ws:// on loopback is fine for development only; use wss:// for a phone")
        else:
            add(FAIL, "livekit.url", "must be wss:// (cleartext ws:// is only acceptable on loopback)")
    add(OK if cfg.livekit_api_key else FAIL, "livekit.key", "LIVEKIT_API_KEY set" if cfg.livekit_api_key else "LIVEKIT_API_KEY is not set")
    secret = cfg.livekit_api_secret.reveal()
    if not secret:
        add(FAIL, "livekit.secret", "LIVEKIT_API_SECRET is not set")
    elif len(secret) < 32:
        add(WARN, "livekit.secret", "LiveKit API secrets are normally 32+ characters; check you copied it whole")
    else:
        add(OK, "livekit.secret", "LIVEKIT_API_SECRET set (server side only; never sent to the phone)")

    # ---- per-call agent credential
    agent_secret = cfg.agent_secret.reveal()
    add(OK if len(agent_secret) >= 32 else FAIL, "agent.secret",
        "per-call worker credential key is set" if len(agent_secret) >= 32 else "HOOD_VOICECALL_AGENT_SECRET is too short (need 32+ characters)")

    # ---- Apple push
    if not cfg.apns_ready:
        missing = [n for n, v in (("APNS_TEAM_ID", cfg.apns_team_id), ("APNS_KEY_ID", cfg.apns_key_id),
                                  ("APNS_KEY_PATH", cfg.apns_key_path), ("APNS_BUNDLE_ID", cfg.apns_bundle_id)) if not v]
        add(WARN, "apns", "Hood cannot ring the phone until these are set: " + ", ".join(missing) +
            " (the phone can still call Hood)")
    else:
        key = Path(cfg.apns_key_path)
        if not key.is_file():
            add(FAIL, "apns.key", "APNS_KEY_PATH does not point at a file")
        else:
            try:
                mode = key.stat().st_mode
                if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
                    add(WARN, "apns.key", "the .p8 key is readable by other users; run chmod 600 on it")
                else:
                    add(OK, "apns.key", "key file present and not group/world readable")
                from cryptography.hazmat.primitives.asymmetric import ec
                from cryptography.hazmat.primitives.serialization import load_pem_private_key
                k = load_pem_private_key(key.read_bytes(), password=None)
                if not isinstance(k, ec.EllipticCurvePrivateKey) or k.curve.name != "secp256r1":
                    add(FAIL, "apns.key.type", "the key is not an EC P-256 key; APNs auth keys are .p8 files from Apple Developer > Keys")
                else:
                    add(OK, "apns.key.type", "EC P-256 key (what APNs token authentication needs)")
            except Exception:
                add(FAIL, "apns.key.type", "the file is not a readable PEM private key (no password-protected keys)")
        if not re.match(_BUNDLE_RE, cfg.apns_bundle_id):
            add(FAIL, "apns.bundle", "APNS_BUNDLE_ID must look like com.yourname.hoodvoice")
        else:
            add(OK, "apns.bundle", f"VoIP topic will be {cfg.apns_bundle_id}.voip (must equal the app's bundle id + .voip)")
        add(INFO, "apns.env", "sandbox host (api.sandbox.push.apple.com): for apps installed from Xcode" if cfg.apns_sandbox
            else "production host (api.push.apple.com): for TestFlight/App Store builds. A phone's push tokens work on ONE of the two")

    # ---- the worker's model key (name only; never the value)
    if _env_present("GOOGLE_API_KEY") or _env_present("GEMINI_API_KEY"):
        add(OK, "worker.gemini", "a Gemini key is present in this environment (the worker process needs it too)")
    else:
        add(WARN, "worker.gemini", "no GOOGLE_API_KEY/GEMINI_API_KEY[_FILE] here; the voice WORKER needs one (realtime mode). Ignore this on the Hood host if the worker runs elsewhere")
    for mod, label in (("livekit.agents", "livekit-agents"), ("livekit.plugins.google", "livekit-plugins-google"),
                       ("livekit.plugins.silero", "livekit-plugins-silero")):
        try:
            present = module_available(mod)
        except Exception:
            present = False
        add(OK if present else INFO, "worker.dep." + label, "installed" if present else "not installed here (needed only on the machine that runs the worker: pip install -r requirements-voice.txt)")

    # ---- provider, price freshness and caps
    try:
        prof = get_profile(cfg.provider)
        age = ""
        try:
            days = (today - _dt.date.fromisoformat(prof.as_of)).days
            age = f", prices read {prof.as_of} ({days} days ago)"
            if days > 90:
                add(WARN, "provider.prices", f"the price table for {prof.id} is {days} days old; re-check the vendor's pricing page before trusting cost estimates")
        except ValueError:
            pass
        add(OK if prof.quoted else WARN, "provider",
            f"{prof.id} ({prof.mode}, {prof.model}); worst case ${prof.worst_case_usd_per_minute:.4f}/min{age}" +
            ("" if prof.quoted else "; price is NOT from the vendor's own page"))
    except Exception:
        add(FAIL, "provider", f"HOOD_VOICECALL_PROVIDER={cfg.provider!r} is not one of {sorted(PROFILES)}")
    add(INFO, "limits", f"per call ${cfg.per_call_usd:.2f}, day ${cfg.daily_usd:.2f}/{cfg.daily_minutes} min, month ${cfg.monthly_usd:.2f}/{cfg.monthly_minutes} min, "
        f"max call {cfg.max_call_seconds // 60} min, Hood-initiated at most {cfg.max_calls_per_hour}/hour")
    add(INFO, "worker.hood_url", "the worker reaches Hood at HOOD_API_URL (default http://127.0.0.1:8990): keep that on a private network")
    return out


def create_pairing_code(data_dir: Path, owner_id: str = ROOT_OWNER_ID) -> Dict[str, object]:
    return DeviceRegistry(VoiceStore(Path(data_dir))).create_pairing_code(owner_id)


def list_devices(data_dir: Path, owner_id: str = ROOT_OWNER_ID) -> List[Dict[str, object]]:
    return DeviceRegistry(VoiceStore(Path(data_dir))).list(owner_id)


def revoke_device(data_dir: Path, device_id: str, owner_id: str = ROOT_OWNER_ID) -> bool:
    return DeviceRegistry(VoiceStore(Path(data_dir))).revoke(owner_id, device_id)


def format_checks(checks: List[Check]) -> str:
    return "\n".join(f"[{c.level:>4}] {c.name:<18} {c.message}" for c in checks)


def exit_code(checks: List[Check]) -> int:
    return 1 if any(c.level == FAIL for c in checks) else 0
