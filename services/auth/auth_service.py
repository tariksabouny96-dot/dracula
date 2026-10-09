"""
HOOD Authentication & Multi-User Access Management Service
Governed by Master System Specification v1.2 Authentication & Multi-User Access Addendum.

Key Components:
1. Permanent Root Owner (ZACK / Zakaria):
   - Highest human authority.
   - Protected identity; cannot be demoted, deleted, replaced, or impersonated.
   - Initialized on first-run with owner password and a protected one-time recovery key.
2. Secure Credential Hashing:
   - Scrypt KDF (N=16384, r=8, p=1) + cryptographically random 16-byte salt.
   - Timing-safe comparisons; zero plaintext passwords stored.
3. Cryptographic Session Management:
   - 32-byte high-entropy session tokens.
   - Configurable session expiration, idle timeouts, and explicit revocation.
4. Role-Based Access Control (RBAC):
   - ROOT_OWNER, ADMINISTRATOR, MANAGER, OPERATOR, VIEWER.
   - Granular permissions and project isolation.
   - Backend endpoint & tool execution enforcement.
5. Root-Only Protected Powers:
   - Ownership management, constitutional governance changes, X activation,
     financial policy modifications, global user admin.
6. Anti-Enumeration & CSRF:
   - Generic authentication failure errors.
   - Per-session CSRF tokens.
"""

from __future__ import annotations
import hmac
import hashlib
import os
import re
import json
import sqlite3
import secrets
import time
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field


def validate_password_strength(password: str) -> Tuple[bool, str]:
    """
    Validates password strength according to HOOD Root Owner security specifications:
    - Minimum 10 characters
    - At least one uppercase letter (A-Z)
    - At least one lowercase letter (a-z)
    - At least one digit (0-9) or special symbol
    """
    if not password or len(password) < 10:
        return False, "Password must be at least 10 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter."
    if not re.search(r"[0-9!@#$%^&*()_+\-=\[\]{};':\",.<>/?\\|`~]", password):
        return False, "Password must contain at least one digit or special symbol."
    return True, ""


class RateLimiter:
    """
    In-memory sliding-window rate limiter for sensitive authentication actions.
    Default: max 5 failed attempts within 15 minutes (900s).
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 900):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts: Dict[str, List[float]] = {}

    def is_rate_limited(self, key: str) -> Tuple[bool, int]:
        """
        Returns (is_limited, seconds_remaining).
        """
        now = time.time()
        record = self.attempts.get(key, [])
        valid_attempts = [t for t in record if now - t < self.window_seconds]
        self.attempts[key] = valid_attempts

        if len(valid_attempts) >= self.max_attempts:
            oldest_relevant = valid_attempts[0]
            remaining = int(self.window_seconds - (now - oldest_relevant))
            return True, max(1, remaining)
        return False, 0

    def record_failure(self, key: str):
        now = time.time()
        if key not in self.attempts:
            self.attempts[key] = []
        self.attempts[key].append(now)

    def reset(self, key: str):
        self.attempts.pop(key, None)


class UserRole(str, Enum):
    ROOT_OWNER = "ROOT_OWNER"
    ADMINISTRATOR = "ADMINISTRATOR"
    MANAGER = "MANAGER"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class UserPermission(str, Enum):
    # Root-only protected powers
    OWNERSHIP_ADMIN = "OWNERSHIP_ADMIN"
    CONSTITUTION_ADMIN = "CONSTITUTION_ADMIN"
    X_ACTIVATION = "X_ACTIVATION"
    FINANCIAL_ADMIN = "FINANCIAL_ADMIN"
    GLOBAL_USER_ADMIN = "GLOBAL_USER_ADMIN"
    SECRETS_ADMIN = "SECRETS_ADMIN"
    
    # Operational permissions
    CHAT_INTERACTION = "CHAT_INTERACTION"
    VOICE_INTERACTION = "VOICE_INTERACTION"
    EXECUTE_OBJECTIVE = "EXECUTE_OBJECTIVE"
    VIEW_TELEMETRY = "VIEW_TELEMETRY"
    VIEW_PROJECT_DATA = "VIEW_PROJECT_DATA"
    MODIFY_PROJECT_CODE = "MODIFY_PROJECT_CODE"
    APPROVE_ACTIONS = "APPROVE_ACTIONS"
    EMERGENCY_STOP = "EMERGENCY_STOP"


ROLE_DEFAULT_PERMISSIONS: Dict[UserRole, Set[UserPermission]] = {
    UserRole.ROOT_OWNER: set(UserPermission), # All permissions
    UserRole.ADMINISTRATOR: {
        UserPermission.CHAT_INTERACTION,
        UserPermission.VOICE_INTERACTION,
        UserPermission.EXECUTE_OBJECTIVE,
        UserPermission.VIEW_TELEMETRY,
        UserPermission.VIEW_PROJECT_DATA,
        UserPermission.MODIFY_PROJECT_CODE,
        UserPermission.APPROVE_ACTIONS,
        UserPermission.EMERGENCY_STOP
    },
    UserRole.MANAGER: {
        UserPermission.CHAT_INTERACTION,
        UserPermission.VOICE_INTERACTION,
        UserPermission.EXECUTE_OBJECTIVE,
        UserPermission.VIEW_TELEMETRY,
        UserPermission.VIEW_PROJECT_DATA,
        UserPermission.EMERGENCY_STOP
    },
    UserRole.OPERATOR: {
        UserPermission.CHAT_INTERACTION,
        UserPermission.VOICE_INTERACTION,
        UserPermission.VIEW_TELEMETRY,
        UserPermission.VIEW_PROJECT_DATA,
        UserPermission.EMERGENCY_STOP
    },
    UserRole.VIEWER: {
        UserPermission.VIEW_TELEMETRY,
        UserPermission.VIEW_PROJECT_DATA,
        UserPermission.EMERGENCY_STOP
    }
}


class UserRecord(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool = True
    password_hash: str
    salt_hex: str
    created_at: str
    last_login: Optional[str] = None
    custom_permissions: List[UserPermission] = Field(default_factory=list)
    assigned_projects: List[str] = Field(default_factory=lambda: ["*"])
    recovery_hash: Optional[str] = None
    recovery_salt_hex: Optional[str] = None


class UserSession(BaseModel):
    session_token: str
    csrf_token: str
    user_id: str
    username: str
    role: UserRole
    created_at: str
    expires_at: str
    is_revoked: bool = False
    ip_address: Optional[str] = "127.0.0.1"
    user_agent: Optional[str] = "HOOD Desktop Client"


class AuthenticationService:
    """Manages Root Owner initialization, credential verification, RBAC, and sessions."""

    def __init__(self, db_path: Optional[Path] = None, session_ttl_hours: int = 12):
        self.db_path = db_path or Path("artifacts/auth.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self.rate_limiter = RateLimiter(max_attempts=5, window_seconds=900)
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                password_hash TEXT NOT NULL,
                salt_hex TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT,
                custom_permissions TEXT NOT NULL,
                assigned_projects TEXT NOT NULL,
                recovery_hash TEXT,
                recovery_salt_hex TEXT
            );
            """)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_token TEXT PRIMARY KEY,
                csrf_token TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_revoked INTEGER NOT NULL DEFAULT 0,
                ip_address TEXT,
                user_agent TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
            """)
            # Migration check: ensure ip_address and user_agent exist if sessions was created earlier
            cursor = conn.execute("PRAGMA table_info(sessions)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "ip_address" not in existing_cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN ip_address TEXT;")
            if "user_agent" not in existing_cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN user_agent TEXT;")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS access_logs (
                log_id TEXT PRIMARY KEY,
                user_id TEXT,
                username TEXT,
                event_type TEXT NOT NULL,
                ip_address TEXT,
                success INTEGER NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL
            );
            """)

    @staticmethod
    def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
        """Hashes password using scrypt KDF (N=16384, r=8, p=1)."""
        if salt is None:
            salt = os.urandom(16)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
        return derived.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
        salt = bytes.fromhex(salt_hex)
        expected_derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(expected_derived.hex(), password_hash)

    def is_initialized(self) -> bool:
        """Returns True if Root Owner has already been established."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM users WHERE role = 'ROOT_OWNER'").fetchone()
            return row is not None

    def initialize_root_owner(
        self,
        username: str,
        display_name: str,
        password: str
    ) -> Dict[str, Any]:
        """
        First-run setup: creates permanent Root Owner (Zakaria) and generates
        a single-use, protected recovery key.
        """
        if self.is_initialized():
            raise PermissionError("HOOD is already initialized with a Root Owner. Unauthenticated takeover blocked.")

        is_valid, msg = validate_password_strength(password)
        if not is_valid:
            raise ValueError(f"Root Owner password does not meet security requirements: {msg}")

        user_id = "user_root_owner_01"
        pw_hash, salt_hex = self.hash_password(password)

        # Generate cryptographic 32-character recovery key (hex)
        recovery_key = secrets.token_hex(16)
        rec_hash, rec_salt = self.hash_password(recovery_key)

        now = datetime.now(timezone.utc).isoformat()
        user = UserRecord(
            user_id=user_id,
            username=username.strip().lower(),
            display_name=display_name.strip() or "Zakaria",
            role=UserRole.ROOT_OWNER,
            is_active=True,
            password_hash=pw_hash,
            salt_hex=salt_hex,
            created_at=now,
            custom_permissions=list(ROLE_DEFAULT_PERMISSIONS[UserRole.ROOT_OWNER]),
            assigned_projects=["*"],
            recovery_hash=rec_hash,
            recovery_salt_hex=rec_salt
        )

        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO users (
                user_id, username, display_name, role, is_active,
                password_hash, salt_hex, created_at, custom_permissions,
                assigned_projects, recovery_hash, recovery_salt_hex
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user.user_id,
                user.username,
                user.display_name,
                user.role.value,
                1 if user.is_active else 0,
                user.password_hash,
                user.salt_hex,
                user.created_at,
                json.dumps([p.value for p in user.custom_permissions]),
                json.dumps(user.assigned_projects),
                user.recovery_hash,
                user.recovery_salt_hex
            ))

        self._log_access(user.user_id, user.username, "ROOT_OWNER_INITIALIZED", success=True, details="First-run Root Owner setup complete")
        return {
            "user_id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role.value,
            "one_time_recovery_key": recovery_key
        }

    def authenticate(
        self,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> Optional[UserSession]:
        """Authenticates user credentials and issues a secure session token."""
        uname = username.strip().lower()
        ip = ip_address or "127.0.0.1"
        rate_key = f"login:{ip}:{uname}"

        is_limited, remaining = self.rate_limiter.is_rate_limited(rate_key)
        if is_limited:
            self._log_access(None, uname, "RATE_LIMIT_BLOCKED", success=False, details=f"Login rate limited for {remaining}s", ip_address=ip)
            raise ValueError(f"Too many failed login attempts. Please wait {remaining} seconds before trying again.")

        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (uname,)).fetchone()
        
        if not row:
            self.rate_limiter.record_failure(rate_key)
            self._log_access(None, uname, "LOGIN_FAILED", success=False, details="User not found", ip_address=ip)
            return None

        if not row["is_active"]:
            self.rate_limiter.record_failure(rate_key)
            self._log_access(row["user_id"], uname, "LOGIN_BLOCKED", success=False, details="Account disabled", ip_address=ip)
            return None

        is_valid = self.verify_password(password, row["password_hash"], row["salt_hex"])
        if not is_valid:
            self.rate_limiter.record_failure(rate_key)
            self._log_access(row["user_id"], uname, "LOGIN_FAILED", success=False, details="Invalid password", ip_address=ip)
            return None

        # Reset rate limiter on successful authentication
        self.rate_limiter.reset(rate_key)

        # Login success -> Generate session
        session_token = secrets.token_hex(32)
        csrf_token = secrets.token_hex(32)
        now = datetime.now(timezone.utc)
        expires = now + self.session_ttl

        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO sessions (session_token, csrf_token, user_id, username, role, created_at, expires_at, is_revoked, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """, (session_token, csrf_token, row["user_id"], row["username"], row["role"], now.isoformat(), expires.isoformat(), ip, user_agent or "HOOD Client"))
            conn.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (now.isoformat(), row["user_id"]))

        self._log_access(row["user_id"], uname, "LOGIN_SUCCESS", success=True, ip_address=ip)

        return UserSession(
            session_token=session_token,
            csrf_token=csrf_token,
            user_id=row["user_id"],
            username=row["username"],
            role=UserRole(row["role"]),
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            is_revoked=False,
            ip_address=ip,
            user_agent=user_agent or "HOOD Client"
        )

    def validate_session(self, session_token: Optional[str]) -> Optional[UserSession]:
        """Validates session token, checks expiration, and checks account status."""
        if not session_token:
            return None

        with self._get_connection() as conn:
            row = conn.execute("""
            SELECT s.*, u.is_active FROM sessions s
            JOIN users u ON s.user_id = u.user_id
            WHERE s.session_token = ? AND s.is_revoked = 0
            """, (session_token,)).fetchone()

            if not row or not row["is_active"]:
                return None

            now = datetime.now(timezone.utc)
            expires_at = datetime.fromisoformat(row["expires_at"])
            if now > expires_at:
                return None

            return UserSession(
                session_token=row["session_token"],
                csrf_token=row["csrf_token"],
                user_id=row["user_id"],
                username=row["username"],
                role=UserRole(row["role"]),
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                is_revoked=bool(row["is_revoked"]),
                ip_address=row["ip_address"] if "ip_address" in row.keys() else "127.0.0.1",
                user_agent=row["user_agent"] if "user_agent" in row.keys() else "HOOD Client"
            )

    def revoke_session(self, session_token: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("UPDATE sessions SET is_revoked = 1 WHERE session_token = ?", (session_token,))
            return cursor.rowcount > 0

    def revoke_all_user_sessions(self, user_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("UPDATE sessions SET is_revoked = 1 WHERE user_id = ?", (user_id,))
            return cursor.rowcount

    def has_permission(self, user_id: str, permission: UserPermission) -> bool:
        """Backend RBAC check for granular permissions."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT role, custom_permissions, is_active FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not row or not row["is_active"]:
                return False

            role = UserRole(row["role"])
            if role == UserRole.ROOT_OWNER:
                return True

            # Root-only powers can never be granted to non-root users
            root_only_powers = {
                UserPermission.OWNERSHIP_ADMIN,
                UserPermission.CONSTITUTION_ADMIN,
                UserPermission.X_ACTIVATION,
                UserPermission.FINANCIAL_ADMIN,
                UserPermission.GLOBAL_USER_ADMIN,
                UserPermission.SECRETS_ADMIN
            }
            if permission in root_only_powers and role != UserRole.ROOT_OWNER:
                return False

            # Check role defaults + custom grants
            role_perms = ROLE_DEFAULT_PERMISSIONS.get(role, set())
            custom_perms = set(json.loads(row["custom_permissions"])) if row["custom_permissions"] else set()
            return permission in role_perms or permission.value in custom_perms

    def create_user(
        self,
        requester_user_id: str,
        username: str,
        display_name: str,
        password: str,
        role: UserRole,
        assigned_projects: Optional[List[str]] = None
    ) -> UserRecord:
        """Root Owner only: creates new operator/manager/viewer user."""
        if not self.has_permission(requester_user_id, UserPermission.GLOBAL_USER_ADMIN):
            raise PermissionDeniedError("Only ROOT_OWNER can create and manage user accounts.")

        if role == UserRole.ROOT_OWNER:
            raise PermissionDeniedError("Cannot create a second ROOT_OWNER account. Root ownership is singular and permanent.")

        uname = username.strip().lower()
        if not uname or len(uname) < 3:
            raise ValueError("Username must be at least 3 characters long.")
        if not re.match(r"^[a-z0-9_\-\.]+$", uname):
            raise ValueError("Username may only contain letters, numbers, hyphens, periods, and underscores.")

        dname = display_name.strip()
        if not dname or len(dname) < 2:
            raise ValueError("Display name must be at least 2 characters long.")

        is_valid_pw, pw_msg = validate_password_strength(password)
        if not is_valid_pw:
            raise ValueError(f"Password does not meet security requirements: {pw_msg}")

        # Check for existing username collision before insertion
        with self._get_connection() as conn:
            existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (uname,)).fetchone()
            if existing:
                raise ValueError(f"Username '{uname}' is already registered in the system.")

        user_id = f"user_{secrets.token_hex(6)}"
        pw_hash, salt_hex = self.hash_password(password)
        now = datetime.now(timezone.utc).isoformat()

        user = UserRecord(
            user_id=user_id,
            username=uname,
            display_name=dname,
            role=role,
            is_active=True,
            password_hash=pw_hash,
            salt_hex=salt_hex,
            created_at=now,
            custom_permissions=[],
            assigned_projects=assigned_projects or ["default"]
        )

        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO users (
                user_id, username, display_name, role, is_active,
                password_hash, salt_hex, created_at, custom_permissions,
                assigned_projects
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """, (
                user.user_id,
                user.username,
                user.display_name,
                user.role.value,
                user.password_hash,
                user.salt_hex,
                user.created_at,
                json.dumps([]),
                json.dumps(user.assigned_projects)
            ))

        self._log_access(requester_user_id, "ROOT_ADMIN", "USER_CREATED", success=True, details=f"Created user {uname} ({role.value})")
        return user

    def set_user_status(self, requester_user_id: str, target_user_id: str, is_active: bool) -> bool:
        """Root Owner only: enable or disable a user account."""
        if not self.has_permission(requester_user_id, UserPermission.GLOBAL_USER_ADMIN):
            raise PermissionDeniedError("Only ROOT_OWNER can modify account statuses.")

        with self._get_connection() as conn:
            target = conn.execute("SELECT role, username FROM users WHERE user_id = ?", (target_user_id,)).fetchone()
            if not target:
                raise KeyError(f"User {target_user_id} not found.")

            if target["role"] == UserRole.ROOT_OWNER.value:
                raise PermissionDeniedError("ROOT_OWNER account cannot be deactivated or disabled.")

            conn.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (1 if is_active else 0, target_user_id))
            if not is_active:
                conn.execute("UPDATE sessions SET is_revoked = 1 WHERE user_id = ?", (target_user_id,))

        self._log_access(requester_user_id, "ROOT_ADMIN", "STATUS_CHANGE", success=True, details=f"Set {target['username']} active={is_active}")
        return True

    def recover_root_owner_password(
        self,
        one_time_recovery_key: str,
        new_password: str,
        ip_address: Optional[str] = None
    ) -> bool:
        """Owner recovery: allows resetting Root Owner password using protected one-time recovery key."""
        ip = ip_address or "127.0.0.1"
        rate_key = f"recovery:{ip}"

        is_limited, remaining = self.rate_limiter.is_rate_limited(rate_key)
        if is_limited:
            self._log_access(None, "ROOT_RECOVERY", "RATE_LIMIT_BLOCKED", success=False, details=f"Recovery rate limited for {remaining}s", ip_address=ip)
            raise ValueError(f"Too many recovery attempts. Please wait {remaining} seconds before trying again.")

        is_valid, msg = validate_password_strength(new_password)
        if not is_valid:
            raise ValueError(f"New password does not meet security requirements: {msg}")

        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE role = 'ROOT_OWNER'").fetchone()
            if not row or not row["recovery_hash"] or not row["recovery_salt_hex"]:
                self.rate_limiter.record_failure(rate_key)
                return False

            if not self.verify_password(one_time_recovery_key, row["recovery_hash"], row["recovery_salt_hex"]):
                self.rate_limiter.record_failure(rate_key)
                self._log_access(row["user_id"], row["username"], "RECOVERY_FAILED", success=False, details="Invalid recovery key", ip_address=ip)
                return False

            self.rate_limiter.reset(rate_key)
            new_hash, new_salt = self.hash_password(new_password)
            # Invalidate one-time recovery key after single use
            conn.execute("""
            UPDATE users
            SET password_hash = ?, salt_hex = ?, recovery_hash = NULL, recovery_salt_hex = NULL
            WHERE user_id = ?
            """, (new_hash, new_salt, row["user_id"]))
            conn.execute("UPDATE sessions SET is_revoked = 1 WHERE user_id = ?", (row["user_id"],))

        self._log_access(row["user_id"], row["username"], "RECOVERY_SUCCESS", success=True, details="Root Owner password reset via recovery key", ip_address=ip)
        return True

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        ip_address: Optional[str] = None
    ) -> bool:
        """Allows authenticated user to change password after verifying current credentials."""
        ip = ip_address or "127.0.0.1"
        rate_key = f"change_pwd:{ip}:{user_id}"

        is_limited, remaining = self.rate_limiter.is_rate_limited(rate_key)
        if is_limited:
            self._log_access(user_id, None, "RATE_LIMIT_BLOCKED", success=False, details=f"Password change rate limited for {remaining}s", ip_address=ip)
            raise ValueError(f"Too many failed attempts. Please wait {remaining} seconds.")

        with self._get_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise KeyError(f"User {user_id} not found.")

            if not self.verify_password(current_password, user["password_hash"], user["salt_hex"]):
                self.rate_limiter.record_failure(rate_key)
                self._log_access(user_id, user["username"], "PASSWORD_CHANGE_FAILED", success=False, details="Current password incorrect", ip_address=ip)
                raise ValueError("Current password verification failed.")

            is_valid, msg = validate_password_strength(new_password)
            if not is_valid:
                raise ValueError(f"New password does not meet security requirements: {msg}")

            self.rate_limiter.reset(rate_key)
            new_hash, new_salt = self.hash_password(new_password)
            conn.execute("""
            UPDATE users SET password_hash = ?, salt_hex = ? WHERE user_id = ?
            """, (new_hash, new_salt, user_id))
            conn.execute("UPDATE sessions SET is_revoked = 1 WHERE user_id = ?", (user_id,))

        self._log_access(user_id, user["username"], "PASSWORD_CHANGED", success=True, details="Password changed successfully", ip_address=ip)
        return True

    def rotate_recovery_key(
        self,
        user_id: str,
        current_password: str,
        ip_address: Optional[str] = None
    ) -> str:
        """
        Root Owner only: issues a new one-time recovery key upon verifying current password.
        Invalidates previous key, stores only secure scrypt hash, returns plaintext key once.
        """
        ip = ip_address or "127.0.0.1"
        rate_key = f"rotate_key:{ip}:{user_id}"

        is_limited, remaining = self.rate_limiter.is_rate_limited(rate_key)
        if is_limited:
            self._log_access(user_id, None, "RATE_LIMIT_BLOCKED", success=False, details=f"Key rotation rate limited for {remaining}s", ip_address=ip)
            raise ValueError(f"Too many attempts. Please wait {remaining} seconds.")

        with self._get_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not user:
                raise KeyError(f"User {user_id} not found.")

            if user["role"] != UserRole.ROOT_OWNER.value:
                raise PermissionDeniedError("Only ROOT_OWNER can generate recovery keys.")

            if not self.verify_password(current_password, user["password_hash"], user["salt_hex"]):
                self.rate_limiter.record_failure(rate_key)
                self._log_access(user_id, user["username"], "RECOVERY_KEY_ROTATION_FAILED", success=False, details="Current password verification failed", ip_address=ip)
                raise ValueError("Current password verification failed.")

            self.rate_limiter.reset(rate_key)

            # Generate new high-entropy 32-character recovery key (hex)
            new_recovery_key = secrets.token_hex(16)
            rec_hash, rec_salt = self.hash_password(new_recovery_key)

            conn.execute("""
            UPDATE users
            SET recovery_hash = ?, recovery_salt_hex = ?
            WHERE user_id = ?
            """, (rec_hash, rec_salt, user_id))

        self._log_access(user_id, user["username"], "RECOVERY_KEY_ROTATED", success=True, details="New one-time recovery key generated", ip_address=ip)
        return new_recovery_key

    def list_active_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Lists active, unrevoked sessions for a user."""
        with self._get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            rows = conn.execute("""
            SELECT session_token, created_at, expires_at, ip_address, user_agent
            FROM sessions
            WHERE user_id = ? AND is_revoked = 0 AND expires_at > ?
            ORDER BY created_at DESC
            """, (user_id, now)).fetchall()

            return [
                {
                    "session_token_prefix": r["session_token"][:8] + "...",
                    "created_at": r["created_at"],
                    "expires_at": r["expires_at"],
                    "ip_address": r["ip_address"] or "127.0.0.1",
                    "user_agent": r["user_agent"] or "HOOD Client"
                }
                for r in rows
            ]

    def revoke_other_sessions(self, user_id: str, current_session_token: str) -> int:
        """Revokes all sessions for the given user EXCEPT the current session."""
        with self._get_connection() as conn:
            cursor = conn.execute("""
            UPDATE sessions
            SET is_revoked = 1
            WHERE user_id = ? AND session_token != ? AND is_revoked = 0
            """, (user_id, current_session_token))
            count = cursor.rowcount

        self._log_access(user_id, None, "SESSIONS_REVOKED_OTHERS", success=True, details=f"Revoked {count} other active sessions")
        return count

    def list_users(self, requester_user_id: str) -> List[Dict[str, Any]]:
        """Root Owner only: view all users."""
        if not self.has_permission(requester_user_id, UserPermission.GLOBAL_USER_ADMIN):
            raise PermissionDeniedError("Only ROOT_OWNER can view user directory.")

        with self._get_connection() as conn:
            rows = conn.execute("SELECT user_id, username, display_name, role, is_active, created_at, last_login, assigned_projects FROM users").fetchall()
            return [
                {
                    "user_id": r["user_id"],
                    "username": r["username"],
                    "display_name": r["display_name"],
                    "role": r["role"],
                    "is_active": bool(r["is_active"]),
                    "created_at": r["created_at"],
                    "last_login": r["last_login"],
                    "assigned_projects": json.loads(r["assigned_projects"])
                }
                for r in rows
            ]

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Look up user metadata by username."""
        uname = (username or "").strip().lower()
        with self._get_connection() as conn:
            row = conn.execute("SELECT user_id, username, display_name, role, is_active FROM users WHERE username = ?", (uname,)).fetchone()
            if row:
                return {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "display_name": row["display_name"],
                    "role": row["role"],
                    "is_active": bool(row["is_active"])
                }
        return None

    def _log_access(
        self,
        user_id: Optional[str],
        username: Optional[str],
        event_type: str,
        success: bool,
        details: Optional[str] = None,
        ip_address: Optional[str] = None
    ):
        with self._get_connection() as conn:
            log_id = f"log_{secrets.token_hex(8)}"
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("""
            INSERT INTO access_logs (log_id, user_id, username, event_type, ip_address, success, details, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (log_id, user_id, username, event_type, ip_address or "127.0.0.1", 1 if success else 0, details, now))


class PermissionDeniedError(Exception):
    """Raised when an operation violates Role-Based Access Control."""
    pass
