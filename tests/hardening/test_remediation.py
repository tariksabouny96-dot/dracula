"""Offline regression tests for high-risk HOOD remediation boundaries."""
import io
import json
import tarfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from packages.auth.vault import SecretVault
from packages.contracts import ModelRequest, ModelClass
from services.auth.auth_service import AuthenticationService, UserRole
from services.dev_executor.code_modifier import CodeModifier
from services.model_gateway.local_adapter import LocalProviderAdapter
from services.model_gateway.openai_adapter import OpenAIProviderAdapter
from services.model_gateway.base import ProviderNotConfiguredError
from services.nodes.migration import NodeMigrationBundle
from services.sentinel.self_healing import SelfHealingEngine
from services.tool_gateway.gateway import ToolGateway, PermissionDeniedError
from services.tool_gateway.tools import FSWriteFileTool, ShellExecTool
from ui.server import JarvisServer, JarvisUIHandler


def request(base, route, cookie=None, data=None, origin=None):
    headers = {}
    if cookie:
        headers['Cookie'] = cookie
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if origin:
        headers['Origin'] = origin
    req = urllib.request.Request(base + route, headers=headers, data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


@pytest.fixture
def ui_server(tmp_path):
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('SAFE_STATIC_FILE', encoding='utf8')
    (tmp_path / 'outside.txt').write_text('TOP_SECRET_OUTSIDE', encoding='utf8')
    original = JarvisUIHandler.ui_dir
    JarvisUIHandler.ui_dir = static.resolve()
    auth = AuthenticationService(db_path=tmp_path / 'auth.db')
    root = auth.initialize_root_owner('owner', 'Owner', 'OwnerPassword123!')
    viewer = auth.create_user(root['user_id'], 'viewer', 'Viewer', 'ViewerPassword123!', UserRole.VIEWER)
    srv = JarvisServer(port=0, auth_service=auth)
    srv.start()
    base = 'http://127.0.0.1:' + str(srv.httpd.server_address[1])
    try:
        owner_session = auth.authenticate('owner', 'OwnerPassword123!')
        viewer_session = auth.authenticate('viewer', 'ViewerPassword123!')
        yield base, f'hood_session={owner_session.session_token}', f'hood_session={viewer_session.session_token}', auth
    finally:
        srv.stop()
        if srv.thread:
            srv.thread.join(timeout=2)
        JarvisUIHandler.ui_dir = original


def test_static_root_does_not_serve_parent_files(ui_server):
    base, _, _, _ = ui_server
    code, body, _ = request(base, '/static/%2e%2e/outside.txt')
    assert code == 404 and b'TOP_SECRET_OUTSIDE' not in body
    code, body, _ = request(base, '/static/index.html')
    assert code == 200 and b'SAFE_STATIC_FILE' in body


def test_http_permissions_and_origin_block(ui_server):
    base, owner, viewer, _ = ui_server
    assert request(base, '/api/memory/list')[0] == 401
    assert request(base, '/api/memory/list', viewer)[0] == 403
    assert request(base, '/api/approvals/resolve', viewer, {'approval_id': 'fake', 'approved': True})[0] == 403
    assert request(base, '/api/chat', viewer, {'text': 'wake X'})[0] == 403
    assert request(base, '/api/chat', owner, {'text': 'hello'}, origin='http://not-the-app.test')[0] == 403
    assert request(base, '/api/sentinel/x/activate', owner, {'approved': True})[0] != 200
    assert request(base, '/api/chat', owner, {'text': 'hello', 'modality': 'voice'})[0] == 503


def test_session_listing_does_not_expose_tokens(ui_server):
    base, owner, _, _ = ui_server
    code, body, _ = request(base, '/api/auth/sessions', owner)
    assert code == 200
    listed = json.loads(body)
    assert listed and all('full_token' not in item for item in listed)


def test_vault_key_random_and_corruption_fails_closed(tmp_path):
    vault_path = tmp_path / 'vault.enc'
    vault = SecretVault(vault_path=vault_path)
    vault.set_secret('demo', 'token', 'dummy-value')
    saved_bytes = vault_path.read_bytes()
    assert (tmp_path / '.vault_key').exists()
    (tmp_path / '.vault_key').write_bytes(b'invalid-encryption-key')
    with pytest.raises((ValueError, Exception)):
        SecretVault(vault_path=vault_path)
    assert vault_path.read_bytes() == saved_bytes


def test_shell_never_runs_and_grants_required(tmp_path):
    gateway = ToolGateway(workspace_root=tmp_path)
    gateway.register_tool(FSWriteFileTool(gateway))
    gateway.register_tool(ShellExecTool(gateway))
    with pytest.raises(PermissionDeniedError):
        gateway.invoke_tool('fs_write_file', {'path': 'test.txt', 'content': 'unsafe'})
    assert not (tmp_path / 'test.txt').exists()
    with pytest.raises(PermissionDeniedError):
        ShellExecTool(gateway).execute({'command': 'echo anything'})


def test_cross_target_checkpoint_refusal(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'a.txt').write_text('a')
    (root / 'b.txt').write_text('b')
    mod = CodeModifier(workspace_root=root)
    ref = mod.create_file_checkpoint(root / 'a.txt')
    with pytest.raises(PermissionError):
        mod.restore_file_checkpoint('b.txt', ref)
    assert (root / 'b.txt').read_text() == 'b'


def test_malicious_migration_archive_no_extraction(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    bundle = NodeMigrationBundle(workspace_root=root)
    bad = tmp_path / 'bad.tar.gz'
    with tarfile.open(bad, 'w:gz') as tar:
        raw = b'bad'
        info = tarfile.TarInfo('../escape.txt')
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
    output = root / 'mem.db'
    with pytest.raises(ValueError):
        bundle.import_node(bad, output)
    assert not output.exists() and not (tmp_path / 'escape.txt').exists()


def test_local_model_uses_real_contract_and_explicit_mock(monkeypatch):
    class DummyReply:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_args): return json.dumps({'choices': [{'message': {'content': 'local ok'}}], 'usage': {'prompt_tokens': 4, 'completion_tokens': 2, 'total_tokens': 6}}).encode()
    class DummyOpener:
        def open(self, *_args, **_kw): return DummyReply()
    monkeypatch.setattr('urllib.request.build_opener', lambda *_args, **_kw: DummyOpener())
    adapter = LocalProviderAdapter(model_name='testing-model')
    response = adapter.invoke(ModelRequest(prompt='sample', model_class=ModelClass.FAST))
    assert response.text == 'local ok'
    assert response.model_name == 'testing-model'
    assert response.usage.total_tokens == 6


def test_self_heal_cannot_delete_unrelated_path(tmp_path):
    from services.sentinel.self_healing import SelfHealingEngine
    untrusted = tmp_path / 'personal_files'
    untrusted.mkdir()
    (untrusted / 'retain.txt').write_text('important')
    engine = SelfHealingEngine(actions_log=tmp_path / 'actions.log')
    result = engine.repair_disposable_cache(untrusted)
    assert not result.success
    assert (untrusted / 'retain.txt').read_text() == 'important'


def test_approval_hash_binding_and_replay_are_enforced(tmp_path):
    import hashlib
    from packages.contracts import RiskLevel
    from services.policy.approval_service import ApprovalService
    approval_svc = ApprovalService()
    gateway = ToolGateway(workspace_root=tmp_path, approval_service=approval_svc)
    tool = FSWriteFileTool(gateway)
    gateway.register_tool(tool)
    grant = gateway.issue_capability_grant('fs:write', 'test.txt', 'Hood', ttl_seconds=60)
    approved_params = {'path': 'test.txt', 'content': 'expected'}
    canonical = json.dumps(approved_params, sort_keys=True, separators=(',', ':'), default=str).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    req = approval_svc.create_request('task-demo', 'fs_write_file', 'test.txt', 'Test exact-bound parameters', RiskLevel.L3, options=[{'parameter_sha256': digest}])
    approval_svc.resolve_request(req.approval_id, approved=True, resolved_by='owner')
    with pytest.raises(PermissionDeniedError, match='parameter hash'):
        gateway.invoke_tool('fs_write_file', {'path': 'test.txt', 'content': 'tampered'}, approval_id=req.approval_id)
    assert not (tmp_path / 'test.txt').exists()
    result = gateway.invoke_tool('fs_write_file', approved_params, approval_id=req.approval_id)
    assert result['status'] == 'success'
    assert (tmp_path / 'test.txt').read_text() == 'expected'
    with pytest.raises(PermissionDeniedError, match='already been used'):
        gateway.invoke_tool('fs_write_file', approved_params, approval_id=req.approval_id)
