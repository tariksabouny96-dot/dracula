"""Self-learning: reinforcement climbs the trust ladder; ESTABLISHED is owner-only."""
import pytest

from packages.contracts import LearningStatus
from services.memory.service import MemoryService
from services.learning.service import LearningService, CANDIDATE_AT, PROVISIONAL_AT


def _svc(tmp_path):
    mem = MemoryService(db_path=tmp_path / "mem.db")
    return LearningService(mem, data_dir=tmp_path / "learn"), mem


def test_reinforcement_promotes_through_candidate_and_provisional(tmp_path):
    svc, _ = _svc(tmp_path)
    v = None
    for _ in range(PROVISIONAL_AT):
        v = svc.record_outcome("alice", "networking", "retry on 503 with backoff", evidence="mission-x")
    assert v["observations"] == PROVISIONAL_AT
    assert v["status"] == LearningStatus.PROVISIONAL.value


def test_candidate_threshold(tmp_path):
    svc, _ = _svc(tmp_path)
    for i in range(CANDIDATE_AT):
        v = svc.record_outcome("alice", "build", "pin dependency versions")
    assert v["status"] == LearningStatus.CANDIDATE.value
    # One sighting stays an OBSERVATION.
    w = svc.record_outcome("alice", "build", "a different lesson")
    assert w["status"] == LearningStatus.OBSERVATION.value


def test_established_requires_owner(tmp_path):
    svc, _ = _svc(tmp_path)
    for _ in range(PROVISIONAL_AT):
        v = svc.record_outcome("alice", "sec", "validate all untrusted input at the boundary")
    lesson_id = v["lesson_id"]
    # HOOD (not owner) cannot establish.
    with pytest.raises(PermissionError):
        svc.establish(lesson_id, is_root_owner=False)
    # Owner can.
    established = svc.establish(lesson_id, approver="Zak", is_root_owner=True)
    assert established["status"] == LearningStatus.ESTABLISHED.value


def test_recall_is_principal_scoped_and_trust_ranked(tmp_path):
    svc, _ = _svc(tmp_path)
    for _ in range(CANDIDATE_AT):
        svc.record_outcome("alice", "ops", "checkpoint before risky changes")
    svc.record_outcome("bob", "ops", "alice-only secret lesson")
    alice = svc.recall("alice", min_status=LearningStatus.CANDIDATE)
    assert len(alice) == 1 and "checkpoint" in alice[0]["content"]
    # Bob's single observation is below CANDIDATE and never leaks to alice.
    assert all("secret" not in v["content"] for v in alice)
    assert svc.recall("bob", min_status=LearningStatus.CANDIDATE) == []


def test_pending_promotions_lists_provisional(tmp_path):
    svc, _ = _svc(tmp_path)
    for _ in range(PROVISIONAL_AT):
        svc.record_outcome("alice", "qa", "run the full suite before any push")
    pending = svc.pending_promotions("alice")
    assert len(pending) == 1 and pending[0]["status"] == LearningStatus.PROVISIONAL.value


def test_emergency_stop_blocks_learning(tmp_path):
    from packages.security import StopLatch
    mem = MemoryService(db_path=tmp_path / "mem.db")
    latch = StopLatch()
    svc = LearningService(mem, stop_latch=latch, data_dir=tmp_path / "learn")
    latch.engage("halt")
    with pytest.raises(Exception):
        svc.record_outcome("alice", "x", "y")
