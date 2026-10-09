"""P1-01: durable, per-principal conversation continuity across restart."""
import json

import pytest

from packages.config import SystemConfig
from services.audit.service import AuditService
from services.auth.auth_service import AuthenticationService, UserRole
from services.core.hood_commander import HoodCommander
from services.interaction.conversation_store import ConversationStore
from services.interaction.interaction_service import InteractionService
from services.memory.service import MemoryService
from services.policy.approval_service import ApprovalService
from ui.server import JarvisServer
from tests.hardening.test_remediation import request


def make_interaction(tmp_path, store):
    cfg = SystemConfig()
    approvals = ApprovalService()
    audit = AuditService(db_path=tmp_path / "audit.db")
    memory = MemoryService(db_path=tmp_path / "mem.db")
    svc = InteractionService(commander=HoodCommander(config=cfg, approval_service=approvals, audit_service=audit,
                                                     memory_service=memory),
                             approval_service=approvals, memory_service=memory, config=cfg)
    svc.conversation_store = store
    return svc


def test_history_survives_restart_and_is_per_principal(tmp_path):
    store = ConversationStore(tmp_path / "conv.sqlite3")
    first = make_interaction(tmp_path, store)
    first.handle_text_input("Remember that the launch codename is BLUEBIRD.", session_id="user:alice")
    first.handle_text_input("Hello", session_id="user:bob")

    restarted = make_interaction(tmp_path, ConversationStore(tmp_path / "conv.sqlite3"))
    alice = restarted._get_or_create_session("user:alice")
    assert [m.text for m in alice.messages][0] == "Remember that the launch codename is BLUEBIRD."
    assert len(alice.messages) == 2
    bob = restarted._get_or_create_session("user:bob")
    assert all("BLUEBIRD" not in m.text for m in bob.messages)

    assert restarted.clear_history("user:alice") == 2
    again = make_interaction(tmp_path, ConversationStore(tmp_path / "conv.sqlite3"))
    assert again._get_or_create_session("user:alice").messages == []


def test_history_api_returns_only_callers_messages_and_erases(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    root = auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    auth.create_user(root["user_id"], "admin2", "Admin Two", "AdminPassword123!", UserRole.ADMINISTRATOR)
    interaction = make_interaction(tmp_path, ConversationStore(tmp_path / "conv.sqlite3"))
    server = JarvisServer(port=0, auth_service=auth, interaction_service=interaction)
    server.start()
    base = "http://127.0.0.1:" + str(server.httpd.server_address[1])
    try:
        owner = "hood_session=" + auth.authenticate("owner", "OwnerPassword123!").session_token
        admin = "hood_session=" + auth.authenticate("admin2", "AdminPassword123!").session_token
        assert request(base, "/api/chat", owner, {"text": "private owner note"})[0] == 200
        history = json.loads(request(base, "/api/chat/history", owner)[1])
        assert history["durable"] is True and history["messages"][0]["text"] == "private owner note"
        other = json.loads(request(base, "/api/chat/history", admin)[1])
        assert other["messages"] == []
        assert request(base, "/api/chat/clear", owner, {})[0] == 400
        assert json.loads(request(base, "/api/chat/clear", owner, {"confirm": True})[1])["messages_removed"] == 2
        assert json.loads(request(base, "/api/chat/history", owner)[1])["messages"] == []
    finally:
        server.stop()


def test_casual_yes_does_not_approve_pending_action(tmp_path):
    svc = make_interaction(tmp_path, None)
    req = svc.approval_service.create_request("t", "fs_write_file", "f", "r")
    svc.handle_text_input("yes", session_id="user:alice")
    svc.handle_text_input("sure, go ahead", session_id="user:alice")
    assert svc.approval_service.get_status(req.approval_id).value == "PENDING"
