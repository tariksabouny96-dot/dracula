"""Owner-scoped artifact registry with hashed, single-use download tokens.

Security properties:
- Files are written once (O_EXCL, fsync, mode 0600, binary) and never through a symlink.
- Every row and every read is scoped to an owner id; another owner's artifact or
  token is a 404 (KeyError), and a cross-owner token is never consumed.
- Download tokens are base64url(claims).HMAC-SHA256, bound to owner + artifact,
  expire in 1-600 seconds, recorded in the database, and used up atomically on
  first redemption.
- Every download re-hashes the file and refuses on any mismatch.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TOKEN_ENV = "HOOD_ARTIFACT_TOKEN_KEY"
MIN_TTL, MAX_TTL, DEFAULT_TTL = 1, 600, 300
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
# Windows has no O_NOFOLLOW and no POSIX mode bits: there the profile ACL protects
# HOOD_DATA_DIR, and symlinks are refused with an explicit check instead.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
# Without O_BINARY, Windows opens fds in text mode and rewrites \n as \r\n.
_BINARY = getattr(os, "O_BINARY", 0)
_POSIX = os.name == "posix"


def _refuse_symlink(path: Path) -> None:
    if not _NOFOLLOW and path.is_symlink():
        raise ArtifactError(f"{path.name} is a symlink; refusing to follow it")


class ArtifactError(Exception):
    pass


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class ArtifactRegistry:
    def __init__(self, data_dir: Path, signing_key: Optional[bytes] = None):
        self.dir = Path(data_dir)
        self.blobs = self.dir / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.dir / "registry.db"
        self._lock = threading.Lock()
        self._key = signing_key or self._load_key()
        self._init_db()

    # --------------------------------------------------------------- key
    def _load_key(self) -> bytes:
        env = os.environ.get(TOKEN_ENV)
        if env:
            return env.encode("utf-8")
        key_file = self.dir / ".token_key"
        if key_file.is_file():
            if _POSIX and key_file.stat().st_mode & 0o077:
                raise ArtifactError(f"{key_file} is group/other-accessible; refusing to use it")
            return key_file.read_bytes()
        key = secrets.token_bytes(32)
        fd = os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS artifacts(
                art_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                mime TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
                created_at REAL NOT NULL, path TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS tokens(
                token_id TEXT PRIMARY KEY, art_id TEXT NOT NULL, owner TEXT NOT NULL,
                expires_at REAL NOT NULL, used_at REAL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_art_owner ON artifacts(owner)")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # --------------------------------------------------------------- store
    def store(self, owner: str, name: str, mime: str, data: bytes) -> Dict[str, Any]:
        if not owner:
            raise ArtifactError("owner required")
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")
        art_id = "art_" + secrets.token_hex(16)
        digest = hashlib.sha256(data).hexdigest()
        path = self.blobs / art_id
        # Write once; never follow a symlink; fsync before registering.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _BINARY, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?)",
                         (art_id, owner, name, mime, digest, len(data), time.time(), str(path)))
            conn.commit()
        return {"artifact_id": art_id, "name": name, "mime": mime, "sha256": digest, "bytes": len(data)}

    def list(self, owner: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT art_id,name,mime,sha256,bytes,created_at FROM artifacts WHERE owner=? ORDER BY created_at DESC",
                (owner,)).fetchall()
        return [{"artifact_id": r["art_id"], "name": r["name"], "mime": r["mime"],
                 "sha256": r["sha256"], "bytes": r["bytes"], "created_at": r["created_at"]} for r in rows]

    def get(self, owner: str, art_id: str) -> Dict[str, Any]:
        row = self._row(owner, art_id)
        return {"artifact_id": row["art_id"], "name": row["name"], "mime": row["mime"],
                "sha256": row["sha256"], "bytes": row["bytes"], "created_at": row["created_at"]}

    def _row(self, owner: str, art_id: str) -> sqlite3.Row:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE art_id=? AND owner=?", (art_id, owner)).fetchone()
        if not row:
            raise KeyError(art_id)  # cross-owner or missing -> 404
        return row

    # --------------------------------------------------------------- tokens
    def mint_token(self, owner: str, art_id: str, ttl_seconds: int = DEFAULT_TTL) -> Dict[str, Any]:
        self._row(owner, art_id)  # ownership check (KeyError -> 404)
        ttl = max(MIN_TTL, min(int(ttl_seconds), MAX_TTL))
        token_id = secrets.token_hex(16)
        expires_at = time.time() + ttl
        claims = {"t": token_id, "a": art_id, "o": owner, "exp": int(expires_at)}
        payload = _b64u(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = _b64u(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())
        token = f"{payload}.{sig}"
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO tokens VALUES (?,?,?,?,?)", (token_id, art_id, owner, expires_at, None))
            conn.commit()
        return {"token": token, "expires_at": expires_at, "ttl_seconds": ttl}

    def redeem(self, token: str, expected_owner: Optional[str] = None) -> Tuple[str, str, bytes]:
        """Return (name, mime, data) for a valid, unexpired, unused token, or raise.

        When ``expected_owner`` is given, the token's owner must match it, so an
        authenticated caller can only redeem tokens minted for themselves (a
        stolen token for another owner is a 404 and is not consumed)."""
        try:
            payload, sig = token.split(".", 1)
            expected = _b64u(hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest())
        except Exception:
            raise KeyError("invalid token")
        if not hmac.compare_digest(sig, expected):
            raise KeyError("invalid token")  # tampered -> 404, never reveals more
        try:
            claims = json.loads(_b64u_decode(payload))
        except Exception:
            raise KeyError("invalid token")
        token_id, art_id, owner = claims.get("t"), claims.get("a"), claims.get("o")
        if not (token_id and art_id and owner):
            raise KeyError("invalid token")
        if expected_owner is not None and owner != expected_owner:
            raise KeyError("invalid token")  # another owner's token -> 404, not consumed
        now = time.time()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM tokens WHERE token_id=?", (token_id,)).fetchone()
            if not row or row["art_id"] != art_id or row["owner"] != owner:
                raise KeyError("invalid token")
            if row["used_at"] is not None:
                raise KeyError("token already used")
            if now > row["expires_at"] or now > claims.get("exp", 0):
                raise KeyError("token expired")
            # Atomic single-use: only one redemption flips used_at from NULL.
            cur = conn.execute("UPDATE tokens SET used_at=? WHERE token_id=? AND used_at IS NULL",
                               (now, token_id))
            conn.commit()
            if cur.rowcount != 1:
                raise KeyError("token already used")
        art = self._row(owner, art_id)
        data = self._read_blob(Path(art["path"]))
        if hashlib.sha256(data).hexdigest() != art["sha256"]:
            raise ArtifactError("artifact hash mismatch; refusing to serve a tampered file")
        return art["name"], art["mime"], data

    @staticmethod
    def _read_blob(path: Path) -> bytes:
        _refuse_symlink(path)
        fd = os.open(str(path), os.O_RDONLY | _NOFOLLOW | _BINARY)
        try:
            with os.fdopen(fd, "rb") as f:
                return f.read()
        except Exception:
            os.close(fd)
            raise
