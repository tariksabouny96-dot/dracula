"""Safe synthetic tests: never send real OS input."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest

from packages.contracts import RiskLevel
from services.desktop.desktop_service import DesktopService
from services.policy.approval_service import ApprovalService


class DummyBackend:
    def get_foreground_window(self):
        return SimpleNamespace(hwnd=42)
    def activate_window(self, hwnd):
        return True


def fingerprint(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


@pytest.fixture
def desktop(tmp_path):
    from services.audit.service import AuditService
    service = DesktopService(backend=DummyBackend(), approval_service=ApprovalService(),
                             audit_service=AuditService(db_path=tmp_path / 'audit.db'))
    service.observer.compute_state_hash = lambda: 'hash'
    service.input_ctrl.type_text = lambda text: True
    service.input_ctrl.send_shortcut = lambda code, mods: True
    service.input_ctrl.click = lambda *args: True
    return service


def approve(s, action, params, task='t'):
    h = fingerprint(params)
    req = s.approval_service.create_request(task, action, h, 'Human approval',
                                            risk_level=RiskLevel.L2,
                                            options=[{'parameter_sha256': h}])
    s.approval_service.resolve_request(req.approval_id, True, resolved_by='Owner')
    return req


def test_text_requires_exact_approval_and_one_time_use(desktop):
    req = approve(desktop, 'desktop_type_text', {'text': 'hello', 'target_hwnd': 42})
    with pytest.raises(PermissionError, match='match'):
        desktop.type_into_active('t', 'different', RiskLevel.L2, approval_id=req.approval_id)
    assert desktop.type_into_active('t', 'hello', RiskLevel.L2, approval_id=req.approval_id).success
    with pytest.raises(PermissionError, match='consumed'):
        desktop.type_into_active('t', 'hello', RiskLevel.L2, approval_id=req.approval_id)


def test_expired_approval_denied(desktop):
    req = approve(desktop, 'desktop_type_text', {'text': 'hello', 'target_hwnd': 42})
    req.resolved_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    with pytest.raises(PermissionError, match='expired'):
        desktop.type_into_active('t', 'hello', RiskLevel.L2, approval_id=req.approval_id)


def test_coordinate_click_denied_before_input(desktop):
    called = []
    desktop.input_ctrl.click = lambda *args: called.append(args)
    with pytest.raises(PermissionError, match='approval'):
        desktop.execute_vision_or_coordinate_click('t', 42, 10, 20, 'button', RiskLevel.L3)
    assert not called


def test_shortcut_requires_approval(desktop):
    with pytest.raises(PermissionError, match='approval'):
        desktop.send_shortcut('t', 65, risk_level=RiskLevel.L2)
    req = approve(desktop, 'desktop_shortcut', {'key_code': 65, 'modifiers': [], 'target_hwnd': 42})
    assert desktop.send_shortcut('t', 65, risk_level=RiskLevel.L2, approval_id=req.approval_id).success


def test_emergency_stop_blocks_approved_action(desktop):
    req = approve(desktop, 'desktop_type_text', {'text': 'hello', 'target_hwnd': 42})
    desktop.halt()
    assert not desktop.type_into_active('t', 'hello', RiskLevel.L2, approval_id=req.approval_id).success
