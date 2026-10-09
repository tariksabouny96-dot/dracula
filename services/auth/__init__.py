"""
HOOD Authentication Package
"""
from services.auth.auth_service import (
    AuthenticationService,
    UserRole,
    UserPermission,
    UserRecord,
    UserSession,
    PermissionDeniedError,
    ROLE_DEFAULT_PERMISSIONS
)

__all__ = [
    "AuthenticationService",
    "UserRole",
    "UserPermission",
    "UserRecord",
    "UserSession",
    "PermissionDeniedError",
    "ROLE_DEFAULT_PERMISSIONS"
]
