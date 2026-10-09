import os
import json
import pytest
from pathlib import Path

from services.auth.auth_service import (
    AuthenticationService,
    UserRole,
    UserPermission,
    PermissionDeniedError,
)
from ui.server import JarvisServer


@pytest.fixture
def temp_auth_db(tmp_path):
    db_file = tmp_path / "auth_test.db"
    return db_file


def test_auth_service_root_owner_initialization_and_recovery(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    assert not service.is_initialized()

    # Initialize Root Owner
    root_data = service.initialize_root_owner(
        username="zack",
        password="MyMasterPassword123!",
        display_name="Zack (Zakaria)",
    )
    assert service.is_initialized()
    assert root_data["username"] == "zack"
    assert root_data["role"] == UserRole.ROOT_OWNER.value
    recovery_key = root_data["one_time_recovery_key"]
    assert len(recovery_key) == 32

    # Cannot re-initialize root owner
    with pytest.raises(PermissionError, match="HOOD is already initialized"):
        service.initialize_root_owner("intruder", "IntruderPass", "Intruder")

    # Authenticate with correct credentials
    session = service.authenticate("zack", "MyMasterPassword123!")
    assert session is not None
    assert session.username == "zack"
    assert session.role == UserRole.ROOT_OWNER

    # Authenticate with incorrect credentials
    bad_session = service.authenticate("zack", "WrongPassword")
    assert bad_session is None

    # Anti-enumeration check: unknown user returns None without error
    unknown_session = service.authenticate("unknown_user", "AnyPassword")
    assert unknown_session is None

    # Test Recovery
    # Invalid recovery key fails
    assert not service.recover_root_owner_password("invalid_key", "NewMasterPassword123!")

    # Valid recovery key succeeds
    assert service.recover_root_owner_password(recovery_key, "NewMasterPassword123!")

    # Old password no longer works
    assert service.authenticate("zack", "MyMasterPassword123!") is None

    # New password works
    recovered_session = service.authenticate("zack", "NewMasterPassword123!")
    assert recovered_session is not None

    # Recovery key is single-use and now invalidated
    assert not service.recover_root_owner_password(recovery_key, "AnotherPassword!")


def test_auth_rbac_permissions(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    root_data = service.initialize_root_owner(username="zack", display_name="Zack", password="RootPass123!")
    root_user_id = root_data["user_id"]

    # Root Owner has all permissions
    assert service.has_permission(root_user_id, UserPermission.OWNERSHIP_ADMIN)
    assert service.has_permission(root_user_id, UserPermission.GLOBAL_USER_ADMIN)
    assert service.has_permission(root_user_id, UserPermission.X_ACTIVATION)
    assert service.has_permission(root_user_id, UserPermission.EXECUTE_OBJECTIVE)

    # Root Owner creates an OPERATOR user
    operator_user = service.create_user(
        requester_user_id=root_user_id,
        username="operator_bob",
        display_name="Bob Operator",
        password="OperatorPass123!",
        role=UserRole.OPERATOR,
    )
    assert operator_user.role == UserRole.OPERATOR
    assert service.has_permission(operator_user.user_id, UserPermission.CHAT_INTERACTION)
    assert not service.has_permission(operator_user.user_id, UserPermission.GLOBAL_USER_ADMIN)
    assert not service.has_permission(operator_user.user_id, UserPermission.OWNERSHIP_ADMIN)
    assert not service.has_permission(operator_user.user_id, UserPermission.X_ACTIVATION)

    # Operator cannot create users
    with pytest.raises(PermissionDeniedError, match="Only ROOT_OWNER can create"):
        service.create_user(
            requester_user_id=operator_user.user_id,
            username="another_user",
            display_name="Another",
            password="pass",
            role=UserRole.VIEWER,
        )

    # VIEWER role permissions
    viewer_user = service.create_user(
        requester_user_id=root_user_id,
        username="alice_viewer",
        display_name="Alice Viewer",
        password="ViewerPass123!",
        role=UserRole.VIEWER,
    )
    assert service.has_permission(viewer_user.user_id, UserPermission.VIEW_TELEMETRY)
    assert not service.has_permission(viewer_user.user_id, UserPermission.EXECUTE_OBJECTIVE)


def test_auth_root_protection_and_user_status(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    root_data = service.initialize_root_owner(username="zack", display_name="Zack", password="RootPass123!")
    root_user_id = root_data["user_id"]

    operator_user = service.create_user(
        requester_user_id=root_user_id,
        username="operator_bob",
        display_name="Bob Operator",
        password="OperatorPass123!",
        role=UserRole.OPERATOR,
    )

    # Root owner cannot be deactivated
    with pytest.raises(PermissionDeniedError, match="ROOT_OWNER account cannot be deactivated"):
        service.set_user_status(root_user_id, root_user_id, is_active=False)

    # Cannot create a second ROOT_OWNER account
    with pytest.raises(PermissionDeniedError, match="Cannot create a second ROOT_OWNER"):
        service.create_user(
            requester_user_id=root_user_id,
            username="fake_root",
            display_name="Fake Root",
            password="password",
            role=UserRole.ROOT_OWNER,
        )

    # Deactivate operator
    service.set_user_status(root_user_id, operator_user.user_id, is_active=False)
    # Deactivated operator cannot authenticate
    assert service.authenticate("operator_bob", "OperatorPass123!") is None


def test_session_management_and_revocation(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    root_data = service.initialize_root_owner(username="zack", display_name="Zack", password="RootPass123!")
    root_user_id = root_data["user_id"]

    session = service.authenticate("zack", "RootPass123!")
    assert session is not None
    token = session.session_token

    # Validate active session
    validated = service.validate_session(token)
    assert validated is not None
    assert validated.user_id == root_user_id

    # Revoke session
    assert service.revoke_session(token) is True
    assert service.validate_session(token) is None


def test_server_unauthenticated_emergency_stop_access(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    service.initialize_root_owner(username="zack", display_name="Zack", password="RootPass123!")

    # Verify server initializes with auth service
    server = JarvisServer(port=8998, auth_service=service)
    assert server.auth_service is not None
    assert server.auth_service.is_initialized()


def test_server_http_auth_and_emergency_stop_lifecycle(temp_auth_db):
    import urllib.request
    import urllib.parse
    import time

    service = AuthenticationService(db_path=temp_auth_db)
    # Start server on an ephemeral port
    server = JarvisServer(port=8996, auth_service=service)
    server.start()
    time.sleep(0.5)

    base_url = "http://127.0.0.1:8996"

    try:
        # 1. Uninitialized status check
        req = urllib.request.Request(f"{base_url}/api/auth/status")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["initialized"] is False
            assert data["authenticated"] is False

        # No controller is installed. Never fabricate a successful emergency stop.
        req = urllib.request.Request(f"{base_url}/api/emergency_stop", data=b"{}", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as unavailable:
            urllib.request.urlopen(req)
        assert unavailable.value.code == 503

        # 3. First-run Root Owner Setup
        init_payload = json.dumps({
            "username": "zack",
            "display_name": "Zakaria",
            "password": "MasterOwnerPassword123!"
        }).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/init", data=init_payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "INITIALIZED"
            rec_key = data["one_time_recovery_key"]
            assert len(rec_key) == 32

        # 4. Protected chat endpoint blocked without authentication
        chat_payload = json.dumps({"text": "Hello Hood"}).encode()
        req = urllib.request.Request(f"{base_url}/api/chat", data=chat_payload, headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 401

        # 5. Authenticate via login
        login_payload = json.dumps({"username": "zack", "password": "MasterOwnerPassword123!"}).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            cookie = resp.headers.get("Set-Cookie")
            assert "hood_session=" in cookie
            data = json.loads(resp.read().decode())
            assert data["status"] == "AUTHENTICATED"
            assert data["user"]["role"] == "ROOT_OWNER"

        session_cookie = cookie.split(";")[0]

        # 6. Status check now reports authenticated Root Owner
        req = urllib.request.Request(f"{base_url}/api/auth/status", headers={"Cookie": session_cookie})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            assert data["initialized"] is True
            assert data["authenticated"] is True
            assert data["username"] == "zack"
            assert data["role"] == "ROOT_OWNER"

        # 7. Root Owner creates operator user
        create_payload = json.dumps({
            "username": "operator1",
            "display_name": "Operator One",
            "password": "OperatorPassword123!",
            "role": "OPERATOR"
        }).encode()
        req = urllib.request.Request(f"{base_url}/api/admin/users/create", data=create_payload, headers={"Content-Type": "application/json", "Cookie": session_cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "SUCCESS"
            assert data["user"]["username"] == "operator1"

        # 8. Root Owner lists users
        req = urllib.request.Request(f"{base_url}/api/admin/users", headers={"Cookie": session_cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            users = json.loads(resp.read().decode())
            assert len(users) == 2

        # Emergency-stop is not operational if no controller is attached.
        req = urllib.request.Request(f"{base_url}/api/emergency_stop", data=b"{}", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as unavailable:
            urllib.request.urlopen(req)
        assert unavailable.value.code == 503

        # 10. Operator logs in and receives restricted session
        op_login_payload = json.dumps({"username": "operator1", "password": "OperatorPassword123!"}).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/login", data=op_login_payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            op_cookie = resp.headers.get("Set-Cookie").split(";")[0]

        # 11. Operator attempting admin user list is rejected with 403 Forbidden
        req = urllib.request.Request(f"{base_url}/api/admin/users", headers={"Cookie": op_cookie})
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 403

        # 12. Root Owner logs out
        req = urllib.request.Request(f"{base_url}/api/auth/logout", data=b"{}", headers={"Content-Type": "application/json", "Cookie": session_cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "LOGGED_OUT"

        # 13. Revoked Root Owner session can no longer access protected endpoints
        req = urllib.request.Request(f"{base_url}/api/admin/users", headers={"Cookie": session_cookie})
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 401

    finally:
        server.stop()


def test_password_policy_enforcement(temp_auth_db):
    from services.auth.auth_service import validate_password_strength
    # Short password
    ok, msg = validate_password_strength("Short1!")
    assert not ok
    assert "at least 10 characters" in msg

    # Missing uppercase
    ok, msg = validate_password_strength("nocaps123456!")
    assert not ok
    assert "uppercase" in msg

    # Missing lowercase
    ok, msg = validate_password_strength("ALLCAPS123456!")
    assert not ok
    assert "lowercase" in msg

    # Missing digits/symbols
    ok, msg = validate_password_strength("LettersOnlyNoDigits")
    assert not ok
    assert "digit or special symbol" in msg

    # Valid strong password
    ok, msg = validate_password_strength("CorrectHorseBatteryStaple99!")
    assert ok
    assert msg == ""

    # Test rejection during initialize_root_owner
    service = AuthenticationService(db_path=temp_auth_db)
    with pytest.raises(ValueError, match="security requirements"):
        service.initialize_root_owner("zack", "Zack", "weak")


def test_root_owner_password_change_and_key_rotation(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    root = service.initialize_root_owner("zack", "Zack", "InitialRootPass123!")
    user_id = root["user_id"]
    initial_key = root["one_time_recovery_key"]

    # 1. Change password fails with wrong current password
    with pytest.raises(ValueError, match="Current password verification failed"):
        service.change_password(user_id, "WrongCurrentPass1!", "NewRootPass456!")

    # 2. Change password fails with weak new password
    with pytest.raises(ValueError, match="security requirements"):
        service.change_password(user_id, "InitialRootPass123!", "weak")

    # 3. Change password succeeds with valid credentials
    assert service.change_password(user_id, "InitialRootPass123!", "NewRootPass456!") is True

    # Old password no longer authenticates
    assert service.authenticate("zack", "InitialRootPass123!") is None
    # New password authenticates
    new_sess = service.authenticate("zack", "NewRootPass456!")
    assert new_sess is not None

    # 4. Rotate recovery key fails with wrong password
    with pytest.raises(ValueError, match="Current password verification failed"):
        service.rotate_recovery_key(user_id, "WrongCurrentPass1!")

    # 5. Rotate recovery key succeeds
    new_key = service.rotate_recovery_key(user_id, "NewRootPass456!")
    assert len(new_key) == 32
    assert new_key != initial_key

    # Old recovery key is invalidated
    assert not service.recover_root_owner_password(initial_key, "ResetViaOldKey123!")

    # New recovery key works
    assert service.recover_root_owner_password(new_key, "RecoveredRootPass789!") is True
    assert service.authenticate("zack", "RecoveredRootPass789!") is not None


def test_session_listing_and_selective_revocation(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    root = service.initialize_root_owner("zack", "Zack", "MasterRootPass123!")
    user_id = root["user_id"]

    # Create 3 sessions
    sess1 = service.authenticate("zack", "MasterRootPass123!", ip_address="127.0.0.1", user_agent="Desktop-A")
    sess2 = service.authenticate("zack", "MasterRootPass123!", ip_address="127.0.0.1", user_agent="Desktop-B")
    sess3 = service.authenticate("zack", "MasterRootPass123!", ip_address="127.0.0.1", user_agent="Desktop-C")

    active_sessions = service.list_active_sessions(user_id)
    assert len(active_sessions) == 3

    # Revoke single session (sess2)
    assert service.revoke_session(sess2.session_token) is True
    assert service.validate_session(sess2.session_token) is None
    assert len(service.list_active_sessions(user_id)) == 2

    # Revoke all other sessions keeping sess3
    revoked_count = service.revoke_other_sessions(user_id, current_session_token=sess3.session_token)
    assert revoked_count == 1
    assert service.validate_session(sess1.session_token) is None
    assert service.validate_session(sess3.session_token) is not None
    assert len(service.list_active_sessions(user_id)) == 1


def test_rate_limiting_protection(temp_auth_db):
    service = AuthenticationService(db_path=temp_auth_db)
    service.initialize_root_owner("zack", "Zack", "MasterRootPass123!")

    # Simulate 5 consecutive failed logins
    for _ in range(5):
        assert service.authenticate("zack", "WrongPass123!", ip_address="192.168.1.50") is None

    # 6th attempt should be blocked by rate limiter with ValueError
    with pytest.raises(ValueError, match="Too many failed login attempts"):
        service.authenticate("zack", "MasterRootPass123!", ip_address="192.168.1.50")

    # Different IP is not affected
    valid_sess = service.authenticate("zack", "MasterRootPass123!", ip_address="127.0.0.1")
    assert valid_sess is not None


def test_server_security_endpoints_http(temp_auth_db):
    import urllib.request
    import urllib.parse
    import time

    service = AuthenticationService(db_path=temp_auth_db)
    server = JarvisServer(port=8994, auth_service=service)
    server.start()
    time.sleep(0.5)
    base_url = "http://127.0.0.1:8994"

    try:
        # Initialize
        init_payload = json.dumps({
            "username": "zack",
            "display_name": "Zakaria",
            "password": "MasterOwnerPassword123!"
        }).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/init", data=init_payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            orig_key = data["one_time_recovery_key"]

        # Login
        login_payload = json.dumps({"username": "zack", "password": "MasterOwnerPassword123!"}).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            cookie = resp.headers.get("Set-Cookie").split(";")[0]

        # 1. Change password via API
        cp_payload = json.dumps({
            "current_password": "MasterOwnerPassword123!",
            "new_password": "UpdatedMasterPassword456!"
        }).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/change_password", data=cp_payload, headers={"Content-Type": "application/json", "Cookie": cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "SUCCESS"

        # Security fix: password rotation invalidates every existing session.
        old_req = urllib.request.Request(f"{base_url}/api/auth/sessions", headers={"Cookie": cookie})
        with pytest.raises(urllib.error.HTTPError) as revoked:
            urllib.request.urlopen(old_req)
        assert revoked.value.code == 401
        relogin_payload = json.dumps({"username": "zack", "password": "UpdatedMasterPassword456!"}).encode()
        relogin_req = urllib.request.Request(f"{base_url}/api/auth/login", data=relogin_payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(relogin_req) as resp:
            cookie = resp.headers.get("Set-Cookie").split(";")[0]

        # 2. Rotate recovery key via API
        rk_payload = json.dumps({
            "current_password": "UpdatedMasterPassword456!"
        }).encode()
        req = urllib.request.Request(f"{base_url}/api/auth/rotate_recovery_key", data=rk_payload, headers={"Content-Type": "application/json", "Cookie": cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "SUCCESS"
            new_key = data["one_time_recovery_key"]
            assert len(new_key) == 32
            assert new_key != orig_key

        # 3. List active sessions via API
        req = urllib.request.Request(f"{base_url}/api/auth/sessions", headers={"Cookie": cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            sessions = json.loads(resp.read().decode())
            assert len(sessions) >= 1

        # 4. Revoke other sessions via API
        req = urllib.request.Request(f"{base_url}/api/auth/sessions/revoke_others", data=b"{}", headers={"Content-Type": "application/json", "Cookie": cookie})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert data["status"] == "SUCCESS"

    finally:
        server.stop()


