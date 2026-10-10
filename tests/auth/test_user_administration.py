"""
Test Suite for User Administration & Multi-User Governance
Tests:
1. Valid Operator creation in isolated database fixture.
2. Registry persistence and listing.
3. Duplicate username rejection with clean error.
4. Empty or short display name rejection.
5. Weak password rejection.
6. Non-root user rejection (permission check).
7. Immutability of Root Owner.
8. Audit logging of user creation.
"""

import pytest
import sqlite3
import tempfile
from pathlib import Path

from services.auth.auth_service import (
    AuthenticationService,
    UserRole,
    UserPermission,
    PermissionDeniedError
)

import gc

@pytest.fixture
def isolated_auth():
    """Provides an isolated AuthenticationService instance backed by a temporary SQLite database."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "test_auth.db"
        auth = AuthenticationService(db_path=db_path)
        auth.initialize_root_owner("zack", "Zack (Zakaria)", "InitialMasterPassword123!")
        yield auth, db_path
        del auth
        gc.collect()

def test_create_valid_operator_and_list_registry(isolated_auth):
    auth, _ = isolated_auth
    session = auth.authenticate("zack", "InitialMasterPassword123!")
    assert session is not None

    new_user = auth.create_user(
        requester_user_id=session.user_id,
        username="alex",
        display_name="Alex Operator",
        password="AlexStrongPassword123!",
        role=UserRole.OPERATOR
    )

    assert new_user.username == "alex"
    assert new_user.display_name == "Alex Operator"
    assert new_user.role == UserRole.OPERATOR
    assert new_user.is_active is True

    # Verify listing
    users = auth.list_users(session.user_id)
    assert len(users) == 2
    usernames = [u["username"] for u in users]
    assert "zack" in usernames
    assert "alex" in usernames

def test_duplicate_username_rejection(isolated_auth):
    auth, _ = isolated_auth
    session = auth.authenticate("zack", "InitialMasterPassword123!")

    # Attempt duplicate 'zack'
    with pytest.raises(ValueError) as exc:
        auth.create_user(
            requester_user_id=session.user_id,
            username="zack",
            display_name="Another Zack",
            password="StrongPassword123!",
            role=UserRole.OPERATOR
        )
    assert "already registered" in str(exc.value).lower()

def test_invalid_display_name_and_weak_password_rejection(isolated_auth):
    auth, _ = isolated_auth
    session = auth.authenticate("zack", "InitialMasterPassword123!")

    # Weak password
    with pytest.raises(ValueError) as exc_pw:
        auth.create_user(
            requester_user_id=session.user_id,
            username="bob",
            display_name="Bob User",
            password="weak",
            role=UserRole.OPERATOR
        )
    assert "password does not meet security requirements" in str(exc_pw.value).lower()

    # Empty display name
    with pytest.raises(ValueError) as exc_dname:
        auth.create_user(
            requester_user_id=session.user_id,
            username="bob",
            display_name=" ",
            password="BobStrongPassword123!",
            role=UserRole.OPERATOR
        )
    assert "display name must be at least 2 characters" in str(exc_dname.value).lower()

def test_unauthorized_non_root_user_creation_blocked(isolated_auth):
    auth, _ = isolated_auth
    root_session = auth.authenticate("zack", "InitialMasterPassword123!")
    
    # Create an operator
    operator = auth.create_user(
        requester_user_id=root_session.user_id,
        username="operator_jim",
        display_name="Jim Operator",
        password="JimStrongPassword123!",
        role=UserRole.OPERATOR
    )

    op_session = auth.authenticate("operator_jim", "JimStrongPassword123!")
    assert op_session is not None

    # Operator cannot create users
    with pytest.raises(PermissionDeniedError):
        auth.create_user(
            requester_user_id=op_session.user_id,
            username="charlie",
            display_name="Charlie",
            password="CharlieStrongPass123!",
            role=UserRole.VIEWER
        )

def test_root_owner_immutability(isolated_auth):
    auth, _ = isolated_auth
    root_session = auth.authenticate("zack", "InitialMasterPassword123!")

    # Cannot create a second ROOT_OWNER
    with pytest.raises(PermissionDeniedError) as exc:
        auth.create_user(
            requester_user_id=root_session.user_id,
            username="second_root",
            display_name="Second Root",
            password="SecondRootPass123!",
            role=UserRole.ROOT_OWNER
        )
    assert "cannot create a second root_owner" in str(exc.value).lower()

    # Cannot deactivate ROOT_OWNER
    with pytest.raises(PermissionDeniedError) as exc_deact:
        auth.set_user_status(root_session.user_id, root_session.user_id, False)
    assert "root_owner account cannot be deactivated" in str(exc_deact.value).lower()

def test_user_creation_audit_logging(isolated_auth):
    auth, db_path = isolated_auth
    root_session = auth.authenticate("zack", "InitialMasterPassword123!")

    auth.create_user(
        requester_user_id=root_session.user_id,
        username="audit_test_user",
        display_name="Audit Test User",
        password="AuditPassword123!",
        role=UserRole.OPERATOR
    )

    with auth._get_connection() as conn:
        log = conn.execute("SELECT * FROM access_logs WHERE event_type = 'USER_CREATED' ORDER BY timestamp DESC LIMIT 1").fetchone()
        assert log is not None
        assert "audit_test_user" in log["details"]
        assert log["success"] == 1
