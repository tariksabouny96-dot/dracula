"""Self-learning over HTTP: record is ordinary; establishing is owner-only."""
import json
import pytest

from services.auth.auth_service import AuthenticationService, UserRole
from services.memory.service import MemoryService
from services.learning.service import LearningService, PROVISIONAL_AT
from ui import routes
from ui.server import JarvisServer
from tests.hardening.test_remediation import request


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    routes.load_modules()
    mem = MemoryService(db_path=tmp_path / "mem.db")
    svc = LearningService(mem, data_dir=tmp_path / "learn")
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "operator", "Operator", "OperatorPassword123!", UserRole.OPERATOR)
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    routes.SERVICES["learning"] = svc  # after start() (which pops it when no runtime)
    base = "http://127.0.0.1:" + str(srv.httpd.server_address[1])
    owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
    op = "hood_session=" + auth.authenticate("operator", "OperatorPassword123!").session_token
    try:
        yield base, owner, op, svc
    finally:
        srv.stop()
        routes.SERVICES.pop("learning", None)


def test_record_reinforce_and_owner_establish(server):
    base, owner, op, svc = server
    pid = None
    for _ in range(PROVISIONAL_AT):
        status, body, _ = request(base, "/api/learning/record", owner,
                                  {"category": "ops", "lesson": "back up before risky changes",
                                   "confirm": True})
        assert status == 201, body
        pid = json.loads(body)["lesson_id"]
    assert json.loads(body)["status"] == "PROVISIONAL"

    # Confirm + CSRF enforced.
    assert request(base, "/api/learning/record", owner, {"category": "x", "lesson": "y"})[0] == 400
    assert request(base, "/api/learning/record", owner,
                   {"category": "x", "lesson": "y", "confirm": True}, csrf=False)[0] == 403

    # A non-owner cannot establish settled truth.
    assert request(base, f"/api/learning/lessons/{pid}/establish", op, {"confirm": True})[0] == 403
    # The owner can.
    status, body, _ = request(base, f"/api/learning/lessons/{pid}/establish", owner, {"confirm": True})
    assert status == 200 and json.loads(body)["status"] == "ESTABLISHED"


def test_pending_lists_provisional(server):
    base, owner, op, svc = server
    for _ in range(PROVISIONAL_AT):
        request(base, "/api/learning/record", owner,
                {"category": "qa", "lesson": "run the full suite before pushing", "confirm": True})
    pending = json.loads(request(base, "/api/learning/pending", owner)[1])["pending"]
    assert len(pending) == 1 and pending[0]["status"] == "PROVISIONAL"
