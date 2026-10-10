"""Contract tests for ui/routes.py: modules cannot bypass auth, CSRF or permissions."""
import json
import re

import pytest

from services.auth.auth_service import AuthenticationService, UserRole
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request


@pytest.fixture
def server(tmp_path):
    before = list(routes.ROUTES)

    @routes.route("GET", r"/api/test-feature/(?P<item>[a-z]+)", permission="VIEW_PROJECT_DATA")
    def get_item(ctx):
        if ctx.match["item"] == "missing":
            raise KeyError("nope")
        return {"item": ctx.match["item"], "user": ctx.user_id, "q": ctx.query.get("x")}

    @routes.route("POST", r"/api/test-feature/act", permission="EXECUTE_OBJECTIVE")
    def act(ctx):
        ctx.require_confirm("test action")
        return 201, {"done": True}

    @routes.route("GET", r"/api/test-feature-file", permission="VIEW_PROJECT_DATA")
    def file(ctx):
        return routes.Raw(b"%PDF-1.4 test", "application/pdf", filename="../evil name.pdf")

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
        routes.ROUTES[:] = before


def test_routes_require_auth_permission_and_csrf(server):
    base, owner, viewer = server
    assert request(base, "/api/test-feature/abc")[0] == 401
    status, body, _ = request(base, "/api/test-feature/abc?x=1", viewer)
    assert status == 200 and json.loads(body)["item"] == "abc" and json.loads(body)["q"] == ["1"]
    assert request(base, "/api/test-feature/missing", owner)[0] == 404
    assert request(base, "/api/test-feature/act", viewer, {"confirm": True})[0] == 403
    assert request(base, "/api/test-feature/act", owner, {"confirm": True}, csrf=False)[0] == 403
    assert request(base, "/api/test-feature/act", owner, {})[0] == 400
    assert request(base, "/api/test-feature/act", owner, {"confirm": True})[0] == 201


def test_raw_file_response_is_attachment_with_safe_name(server):
    base, owner, _ = server
    status, body, headers = request(base, "/api/test-feature-file", owner)
    assert status == 200 and body.startswith(b"%PDF")
    assert re.fullmatch(r'attachment; filename="[A-Za-z0-9._-]+"', headers["Content-Disposition"])
    assert ".." not in headers["Content-Disposition"].split("filename=")[1].strip('"').replace("._", "")[:3]
