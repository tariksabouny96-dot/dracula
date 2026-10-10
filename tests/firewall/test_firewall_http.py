"""Firewall management over HTTP: owner-only, CSRF-checked, confirm-gated."""
import json

import pytest

from services.auth.auth_service import AuthenticationService, UserRole
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    routes.load_modules()
    routes.SERVICES.pop("firewall", None)  # fresh firewall under this data dir
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "viewer", "Viewer", "ViewerPassword123!", UserRole.VIEWER)
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
    viewer = "hood_session=" + auth.authenticate("viewer", "ViewerPassword123!").session_token
    try:
        yield base, owner, viewer
    finally:
        srv.stop()
        routes.SERVICES.pop("firewall", None)


def test_only_owner_can_change_rules_and_default_is_deny(server):
    base, owner, viewer = server
    # Anyone authenticated can read; default policy is deny with no rules.
    status, body, _ = request(base, "/api/firewall/rules", viewer)
    assert status == 200 and json.loads(body)["default_policy"] == "deny"

    rule = {"host": "api.example.com", "ports": [443], "note": "test", "confirm": True}
    # A viewer cannot add a rule (needs OWNERSHIP_ADMIN).
    assert request(base, "/api/firewall/rules", viewer, rule)[0] == 403
    # Confirm is required.
    assert request(base, "/api/firewall/rules", owner, {"host": "api.example.com"})[0] == 400
    # CSRF required on cookie POST.
    assert request(base, "/api/firewall/rules", owner, rule, csrf=False)[0] == 403
    # Owner adds it.
    status, body, _ = request(base, "/api/firewall/rules", owner, rule)
    assert status == 201
    rule_id = json.loads(body)["id"]

    # It now appears in the listing.
    rules = json.loads(request(base, "/api/firewall/rules", owner)[1])["rules"]
    assert any(r["id"] == rule_id for r in rules)

    # Viewer cannot revoke; owner can.
    assert request(base, f"/api/firewall/rules/{rule_id}/revoke", viewer, {"confirm": True})[0] == 403
    assert request(base, f"/api/firewall/rules/{rule_id}/revoke", owner, {"confirm": True})[0] == 200
    rules = json.loads(request(base, "/api/firewall/rules", owner)[1])["rules"]
    assert not any(r["id"] == rule_id for r in rules)
