"""Governed self-development: owner is always the final authority, guardrails
are inviolable, and every applied change is reversible."""
import sys
import pytest

from services.evolution.self_development import (
    SelfDevelopmentController, ConstitutionalViolation, is_protected)
from services.policy.approval_service import ApprovalService
from packages.security import StopLatch


def _workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "services" / "demo").mkdir(parents=True)
    (ws / "services" / "demo" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    return ws


def _controller(tmp_path, **kw):
    return SelfDevelopmentController(
        workspace_root=_workspace(tmp_path),
        approval_service=kw.pop("approval", ApprovalService()),
        data_dir=tmp_path / "sd",
        **kw)


def test_guardrail_files_can_never_be_proposed(tmp_path):
    c = _controller(tmp_path)
    for protected in [
        "services/auth/auth_service.py",
        "services/firewall/policy.py",
        "services/policy/approval_service.py",
        "packages/security/stop.py",
        "services/core/emergency_stop.py",
        "services/evolution/self_development.py",
        ".github/workflows/ci.yml",
        "/etc/passwd",
        "hood_cli.py",  # outside modifiable roots
    ]:
        assert is_protected(protected) is True
        with pytest.raises((ConstitutionalViolation, Exception)):
            c.propose(protected, "x = 1\n", "attempt")


def test_propose_records_but_never_writes_live_tree(tmp_path):
    c = _controller(tmp_path)
    target = c.workspace_root / "services" / "demo" / "mod.py"
    before = target.read_text()
    p = c.propose("services/demo/mod.py", "VALUE = 2\n", "bump value")
    assert p["state"] == "AWAITING_APPROVAL"
    assert target.read_text() == before  # live tree untouched before approval


def test_invalid_python_is_rejected_at_propose(tmp_path):
    c = _controller(tmp_path)
    with pytest.raises(Exception):
        c.propose("services/demo/mod.py", "def broken(:\n", "bad")


def test_apply_requires_owner_and_approval(tmp_path):
    appr = ApprovalService()
    c = _controller(tmp_path, approval=appr)
    p = c.propose("services/demo/mod.py", "VALUE = 2\n", "bump")
    # Not owner.
    with pytest.raises(PermissionError):
        c.apply(p["proposal_id"], is_root_owner=False)
    # Owner but no approval yet.
    with pytest.raises(PermissionError):
        c.apply(p["proposal_id"], is_root_owner=True)
    # Approve, then apply works and changes the file.
    appr.resolve_request(p["approval_id"], approved=True, resolved_by="Zak")
    res = c.apply(p["proposal_id"], approver="Zak", is_root_owner=True)
    assert res["state"] == "APPLIED"
    assert (c.workspace_root / "services" / "demo" / "mod.py").read_text() == "VALUE = 2\n"


def test_apply_rolls_back_when_validation_fails(tmp_path):
    appr = ApprovalService()
    # A validate command that always fails forces a rollback.
    c = _controller(tmp_path, approval=appr, validate_command=[sys.executable, "-c", "import sys; sys.exit(1)"])
    target = c.workspace_root / "services" / "demo" / "mod.py"
    original = target.read_text()
    p = c.propose("services/demo/mod.py", "VALUE = 999\n", "bump")
    appr.resolve_request(p["approval_id"], approved=True, resolved_by="Zak")
    res = c.apply(p["proposal_id"], approver="Zak", is_root_owner=True)
    assert res["state"] == "ROLLED_BACK"
    assert target.read_text() == original  # restored


def test_tampered_proposal_content_is_refused(tmp_path):
    appr = ApprovalService()
    c = _controller(tmp_path, approval=appr)
    p = c.propose("services/demo/mod.py", "VALUE = 2\n", "bump")
    appr.resolve_request(p["approval_id"], approved=True, resolved_by="Zak")
    # Swap the stored content after approval -> hash mismatch -> refused.
    (c.dir / "proposals" / p["proposal_id"]).write_text("VALUE = 666\n", encoding="utf-8")
    with pytest.raises(Exception):
        c.apply(p["proposal_id"], approver="Zak", is_root_owner=True)


def test_emergency_stop_blocks_self_development(tmp_path):
    latch = StopLatch()
    c = _controller(tmp_path, stop_latch=latch)
    latch.engage("halt")
    with pytest.raises(Exception):
        c.propose("services/demo/mod.py", "VALUE = 2\n", "bump")
