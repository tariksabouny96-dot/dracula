"""Registered owner devices: the only things that may receive an incoming call or start a call.

A device is registered from an authenticated owner session with an explicit confirmation. The server
returns a 256-bit secret exactly once and stores only its SHA-256 digest. The device's P-256 public key
(a Secure Enclave key on iOS) is stored for step-up confirmations. Device credentials are accepted only
by the voice-call device routes, never by the general API.
"""
from __future__ import annotations

import hmac
import re
import secrets
import time
from typing import Any, Dict, List, Optional

from .contracts import Conflict, Forbidden, NotFound, RateLimited, VoiceError
from .store import VoiceStore
from .tokens import digest_secret, load_p256_public_key, b64u_decode

MAX_DEVICES = 5
PAIRING_TTL_SECONDS = 300
PAIRING_FAILURE_LIMIT = 10          # wrong codes in the window before every open code is burned
PAIRING_FAILURE_WINDOW = 900
_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"   # no 0/O/1/I: easy to read off a screen
_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{16,512}$")      # APNs device tokens are hex


def _public(row) -> Dict[str, Any]:
    return {"device_id": row["device_id"], "name": row["name"], "platform": row["platform"],
            "app_version": row["app_version"], "created": row["created"], "last_seen": row["last_seen"],
            "revoked": bool(row["revoked_at"]), "has_voip_token": bool(row["voip_token"]),
            "has_alert_token": bool(row["alert_token"]), "apns_env": row["apns_env"]}


class DeviceRegistry:
    def __init__(self, store: VoiceStore):
        self.store = store

    # ------------------------------------------------------------------ registration
    def register(self, owner_id: str, name: str, public_key_b64: str, *, platform: str = "ios",
                 app_version: str = "") -> Dict[str, Any]:
        name = (name or "").strip()[:60]
        if not name:
            raise VoiceError("Device name is required")
        try:
            raw = _decode_b64(public_key_b64)
            load_p256_public_key(raw)
        except Exception:
            raise VoiceError("public_key must be a base64 P-256 public key (SPKI DER or X9.63)") from None
        with self.store._db() as db:
            active = db.execute("SELECT COUNT(*) n FROM devices WHERE owner_id=? AND revoked_at IS NULL", (owner_id,)).fetchone()["n"]
            if active >= MAX_DEVICES:
                raise Conflict(f"At most {MAX_DEVICES} devices can be registered; revoke one first")
            device_id = "dev_" + secrets.token_hex(12)
            secret = secrets.token_urlsafe(32)
            db.execute("INSERT INTO devices (device_id, owner_id, name, secret_digest, public_key_b64, platform, "
                       "app_version, created) VALUES (?,?,?,?,?,?,?,?)",
                       (device_id, owner_id, name, digest_secret(secret), _norm_b64(public_key_b64), platform[:20],
                        app_version[:20], time.time()))
        return {"device_id": device_id, "device_secret": secret, "name": name}

    # ------------------------------------------------------------------ pairing (phone has no owner session)
    def create_pairing_code(self, owner_id: str, *, ttl: int = PAIRING_TTL_SECONDS, now: Optional[float] = None) -> Dict[str, Any]:
        """One-time code the owner reads from the console and types into the phone. Single use, 5 minutes,
        only the digest is stored, and a new code retires any earlier unused one."""
        now = time.time() if now is None else now
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
        with self.store._db() as db:
            db.execute("UPDATE pairing_codes SET used=1 WHERE owner_id=? AND used=0", (owner_id,))
            db.execute("INSERT INTO pairing_codes (code_digest, owner_id, created, expires) VALUES (?,?,?,?)",
                       (digest_secret(code), owner_id, now, now + ttl))
        return {"code": code, "expires_in": ttl}

    def pair(self, code: str, name: str, public_key_b64: str, *, platform: str = "ios", app_version: str = "",
             now: Optional[float] = None) -> Dict[str, Any]:
        now = time.time() if now is None else now
        code = (code or "").strip().upper().replace("-", "").replace(" ", "")
        with self.store._db() as db:
            recent = db.execute("SELECT COUNT(*) n FROM pairing_failures WHERE ts>?", (now - PAIRING_FAILURE_WINDOW,)).fetchone()["n"]
            if recent >= PAIRING_FAILURE_LIMIT:
                db.execute("UPDATE pairing_codes SET used=1 WHERE used=0")          # too many guesses: burn every open code
                raise RateLimited("Too many wrong pairing codes; ask for a new code in the console")
            row = db.execute("SELECT * FROM pairing_codes WHERE code_digest=?", (digest_secret(code),)).fetchone() if len(code) == 10 else None
            if row is None or row["used"] or now > row["expires"]:
                db.execute("INSERT INTO pairing_failures (ts) VALUES (?)", (now,))
                raise Forbidden("Invalid or expired pairing code")
            cur = db.execute("UPDATE pairing_codes SET used=1 WHERE code_digest=? AND used=0", (row["code_digest"],))
            if cur.rowcount != 1:
                raise Forbidden("Invalid or expired pairing code")
        return self.register(row["owner_id"], name, public_key_b64, platform=platform, app_version=app_version)

    def authenticate(self, device_id: str, secret: str) -> Optional[Dict[str, Any]]:
        """Return the device row (as dict) or None. Constant-time on the secret digest."""
        if not device_id or not secret or len(device_id) > 64 or len(secret) > 200:
            return None
        with self.store._db() as db:
            row = db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if row is None:
                digest_secret(secret)                         # keep timing similar for unknown ids
                return None
            ok = hmac.compare_digest(row["secret_digest"], digest_secret(secret))
            if not ok or row["revoked_at"]:
                return None
            db.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (time.time(), device_id))
            return dict(row)

    # ------------------------------------------------------------------ management
    def get(self, device_id: str) -> Dict[str, Any]:
        with self.store._db() as db:
            row = db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown device")
        return dict(row)

    def list(self, owner_id: str) -> List[Dict[str, Any]]:
        with self.store._db() as db:
            rows = db.execute("SELECT * FROM devices WHERE owner_id=? ORDER BY created", (owner_id,)).fetchall()
        return [_public(r) for r in rows]

    def revoke(self, owner_id: str, device_id: str) -> bool:
        with self.store._db() as db:
            cur = db.execute("UPDATE devices SET revoked_at=?, voip_token=NULL, alert_token=NULL "
                             "WHERE device_id=? AND owner_id=? AND revoked_at IS NULL", (time.time(), device_id, owner_id))
            return cur.rowcount > 0

    def set_push_tokens(self, device_id: str, *, voip_token: Optional[str] = None, alert_token: Optional[str] = None,
                        apns_env: Optional[str] = None) -> None:
        for t in (voip_token, alert_token):
            if t is not None and t != "" and not _TOKEN_RE.match(t):
                raise VoiceError("Invalid push token")
        if apns_env is not None and apns_env not in ("sandbox", "production"):
            raise VoiceError("apns_env must be sandbox or production")
        sets, vals = [], []
        for col, v in (("voip_token", voip_token), ("alert_token", alert_token), ("apns_env", apns_env)):
            if v is not None:
                sets.append(f"{col}=?")
                vals.append(v or None)
        if sets:
            with self.store._db() as db:
                db.execute(f"UPDATE devices SET {','.join(sets)} WHERE device_id=? AND revoked_at IS NULL", [*vals, device_id])

    def clear_token(self, device_id: str, kind: str) -> None:
        col = {"voip": "voip_token", "alert": "alert_token"}[kind]
        with self.store._db() as db:
            db.execute(f"UPDATE devices SET {col}=NULL WHERE device_id=?", (device_id,))

    def callable_devices(self, owner_id: str) -> List[Dict[str, Any]]:
        """Devices that can ring: registered, not revoked, with a VoIP token."""
        with self.store._db() as db:
            rows = db.execute("SELECT * FROM devices WHERE owner_id=? AND revoked_at IS NULL AND voip_token IS NOT NULL "
                              "ORDER BY last_seen DESC", (owner_id,)).fetchall()
        return [dict(r) for r in rows]

    def notifiable_devices(self, owner_id: str) -> List[Dict[str, Any]]:
        with self.store._db() as db:
            rows = db.execute("SELECT * FROM devices WHERE owner_id=? AND revoked_at IS NULL AND alert_token IS NOT NULL "
                              "ORDER BY last_seen DESC", (owner_id,)).fetchall()
        return [dict(r) for r in rows]


def _norm_b64(s: str) -> str:
    return s.strip().replace("\n", "")


def _decode_b64(s: str) -> bytes:
    s = _norm_b64(s)
    try:
        import base64
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=False) if ("+" in s or "/" in s) else b64u_decode(s)
    except Exception:
        raise ValueError("bad base64") from None
