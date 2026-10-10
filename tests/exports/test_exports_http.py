"""End-to-end HTTP tests for exports/artifacts through a real JarvisServer:
auth, CSRF, permission, cross-tenant isolation, and the verified download flow.
"""
import json

import pytest

from services.auth.auth_service import AuthenticationService, UserRole
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request

SPEC = {"title": "HTTP Report", "blocks": [
    {"type": "heading", "level": 1, "text": "Hello"},
    {"type": "table", "columns": ["a", "b"], "rows": [["=cmd", "1"]]},
]}


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    # Ensure feature modules are registered (idempotent).
    routes.load_modules()
    # Each test gets its own export/artifact services bound to this data dir.
    for name in ("exports", "artifacts"):
        routes.SERVICES.pop(name, None)

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
        for name in ("exports", "artifacts"):
            routes.SERVICES.pop(name, None)


def test_export_requires_confirm_permission_and_auth(server):
    base, owner, viewer = server
    # Unauthenticated.
    assert request(base, "/api/exports", data={"spec": SPEC, "formats": ["pdf"], "confirm": True})[0] == 401
    # Viewer lacks EXECUTE_OBJECTIVE.
    assert request(base, "/api/exports", viewer, {"spec": SPEC, "formats": ["pdf"], "confirm": True})[0] == 403
    # Missing confirm -> 400.
    assert request(base, "/api/exports", owner, {"spec": SPEC, "formats": ["pdf"]})[0] == 400
    # CSRF required on cookie POST.
    assert request(base, "/api/exports", owner, {"spec": SPEC, "formats": ["pdf"], "confirm": True}, csrf=False)[0] == 403


def test_full_export_download_flow_and_cross_tenant(server):
    base, owner, viewer = server
    status, body, _ = request(base, "/api/exports", owner,
                              {"spec": SPEC, "formats": ["pdf", "xlsx", "zip"], "confirm": True})
    assert status == 201, body
    rec = json.loads(body)
    assert rec["status"] == "complete"
    pdf = next(o for o in rec["outputs"] if o["format"] == "pdf")
    art_id = pdf["artifact_id"]

    # Owner can list and get.
    assert json.loads(request(base, "/api/exports", owner)[1])["exports"]
    assert request(base, f"/api/artifacts/{art_id}", owner)[0] == 200

    # A different tenant cannot see the artifact or the export.
    assert request(base, f"/api/artifacts/{art_id}", viewer)[0] == 404
    assert request(base, f"/api/exports/{rec['export_id']}", viewer)[0] == 404

    # Mint a download token and fetch the file; the bytes are a real PDF.
    status, body, _ = request(base, f"/api/artifacts/{art_id}/token", owner, {"ttl_seconds": 60})
    assert status == 201
    token = json.loads(body)["token"]
    status, data, headers = request(base, f"/api/artifacts/download/{token}", owner)
    assert status == 200 and data.startswith(b"%PDF")
    assert "attachment" in headers["Content-Disposition"]

    # Single use: the token is spent.
    assert request(base, f"/api/artifacts/download/{token}", owner)[0] == 404

    # Another tenant cannot redeem a token minted for the owner.
    status, body, _ = request(base, f"/api/artifacts/{art_id}/token", owner, {"ttl_seconds": 60})
    token2 = json.loads(body)["token"]
    assert request(base, f"/api/artifacts/download/{token2}", viewer)[0] == 404


def test_invalid_spec_is_rejected_cleanly(server):
    base, owner, _ = server
    bad = {"title": "x", "blocks": [{"type": "nope"}]}
    assert request(base, "/api/exports", owner, {"spec": bad, "formats": ["pdf"], "confirm": True})[0] == 400
    assert request(base, "/api/exports", owner, {"spec": SPEC, "formats": [], "confirm": True})[0] == 400
