import pytest
from datetime import datetime, timezone, timedelta
from packages.contracts import MemoryObject, MemoryType, LearningStatus
from services.memory.service import MemoryService, GovernanceViolationError

def test_tenant_isolation(tmp_path):
    svc = MemoryService(db_path=tmp_path / "test_mem.db")

    # Client A memory
    mem_a = MemoryObject(
        type=MemoryType.PROJECT,
        content="Confidential project details for Client A",
        project="client_a"
    )
    svc.write_memory(mem_a)

    # Client B memory
    mem_b = MemoryObject(
        type=MemoryType.PROJECT,
        content="Confidential project details for Client B",
        project="client_b"
    )
    svc.write_memory(mem_b)

    # Query for Client A
    results_a = svc.query_memories(project="client_a")
    assert len(results_a) == 1
    assert "Client A" in results_a[0].content

    # Query for Client B - Client A data must never leak
    results_b = svc.query_memories(project="client_b")
    assert len(results_b) == 1
    assert "Client B" in results_b[0].content
    assert all("Client A" not in m.content for m in results_b)

def test_governance_mutation_prevented(tmp_path):
    svc = MemoryService(db_path=tmp_path / "test_mem.db")
    gov_mem = MemoryObject(
        type=MemoryType.GOVERNANCE,
        content="Constitutional Rule H01",
        project="system"
    )
    with pytest.raises(GovernanceViolationError):
        svc.write_memory(gov_mem, caller_agent="Specialist_Agent")

def test_x_sealed_isolation(tmp_path):
    svc = MemoryService(db_path=tmp_path / "test_mem.db")
    sealed_mem = MemoryObject(
        type=MemoryType.X_SEALED,
        content="Restricted pentest finding",
        project="sec_test"
    )
    svc.write_memory(sealed_mem, caller_agent="Zak")

    # Normal Hood query -> omitted
    normal_res = svc.query_memories(project="sec_test", is_x_active=False)
    assert len(normal_res) == 0

    # X Active query -> visible
    x_res = svc.query_memories(project="sec_test", is_x_active=True)
    assert len(x_res) == 1
    assert "pentest finding" in x_res[0].content

def test_learning_promotion(tmp_path):
    svc = MemoryService(db_path=tmp_path / "test_mem.db")
    lesson = MemoryObject(
        type=MemoryType.EXPERIENCE,
        content="Candidate rule: retry on network 503",
        project="dev",
        learning_status=LearningStatus.OBSERVATION
    )
    svc.write_memory(lesson)

    # Promote to provisional
    promoted = svc.promote_lesson(lesson.memory_id, LearningStatus.PROVISIONAL, "Validated on 3 retry tests")
    assert promoted.learning_status == LearningStatus.PROVISIONAL
    assert promoted.verification_status == "VERIFIED"


# --- F25: principal isolation and enforced trust promotion ---

def test_principal_isolation_within_same_project(tmp_path):
    """Two principals sharing a project must never read each other's memory."""
    svc = MemoryService(db_path=tmp_path / "mem.db")
    svc.write_memory(MemoryObject(
        type=MemoryType.PERSONAL, content="alice secret", project="shared", principal="alice"))
    svc.write_memory(MemoryObject(
        type=MemoryType.PERSONAL, content="bob secret", project="shared", principal="bob"))

    alice_view = svc.query_memories(project="shared", principal="alice")
    assert [m.content for m in alice_view] == ["alice secret"]

    bob_view = svc.query_memories(project="shared", principal="bob")
    assert [m.content for m in bob_view] == ["bob secret"]
    assert all("alice" not in m.content for m in bob_view)


def test_system_principal_memory_is_shared_across_principals(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    svc.write_memory(MemoryObject(
        type=MemoryType.SEMANTIC, content="shared fact", project="shared",
        principal=MemoryService.SYSTEM_PRINCIPAL))
    svc.write_memory(MemoryObject(
        type=MemoryType.PERSONAL, content="alice only", project="shared", principal="alice"))

    alice_view = {m.content for m in svc.query_memories(project="shared", principal="alice")}
    assert alice_view == {"shared fact", "alice only"}
    bob_view = {m.content for m in svc.query_memories(project="shared", principal="bob")}
    assert bob_view == {"shared fact"}


def test_principal_none_is_admin_wide_view(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    svc.write_memory(MemoryObject(type=MemoryType.PERSONAL, content="a", project="p", principal="alice"))
    svc.write_memory(MemoryObject(type=MemoryType.PERSONAL, content="b", project="p", principal="bob"))
    everything = {m.content for m in svc.query_memories(project="p")}
    assert everything == {"a", "b"}


def test_lexical_query_respects_principal_isolation(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    svc.write_memory(MemoryObject(
        type=MemoryType.PERSONAL, content="retry on network timeout", project="p", principal="alice"))
    svc.write_memory(MemoryObject(
        type=MemoryType.PERSONAL, content="retry on network timeout", project="p", principal="bob"))
    hits = svc.query_lexical("network retry", project="p", principal="alice")
    assert len(hits) == 1
    assert hits[0].principal == "alice"


def test_trust_promotion_requires_evidence(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    lesson = MemoryObject(type=MemoryType.EXPERIENCE, content="x", project="dev",
                          learning_status=LearningStatus.OBSERVATION)
    svc.write_memory(lesson)
    with pytest.raises(GovernanceViolationError):
        svc.promote_lesson(lesson.memory_id, LearningStatus.CANDIDATE, "   ")


def test_trust_promotion_cannot_move_backward(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    lesson = MemoryObject(type=MemoryType.EXPERIENCE, content="x", project="dev",
                          learning_status=LearningStatus.PROVISIONAL)
    svc.write_memory(lesson)
    with pytest.raises(GovernanceViolationError):
        svc.promote_lesson(lesson.memory_id, LearningStatus.CANDIDATE, "evidence")


def test_trust_promotion_to_established_requires_owner(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    lesson = MemoryObject(type=MemoryType.EXPERIENCE, content="x", project="dev",
                          learning_status=LearningStatus.PROVISIONAL)
    svc.write_memory(lesson)
    # An agent (no owner authority) cannot grant full trust.
    with pytest.raises(GovernanceViolationError):
        svc.promote_lesson(lesson.memory_id, LearningStatus.ESTABLISHED, "evidence", promoted_by="Engineer")
    # The owner can.
    promoted = svc.promote_lesson(lesson.memory_id, LearningStatus.ESTABLISHED, "owner verified", promoted_by="Zak")
    assert promoted.learning_status == LearningStatus.ESTABLISHED


def test_trust_promotion_never_reaches_superseded(tmp_path):
    svc = MemoryService(db_path=tmp_path / "mem.db")
    lesson = MemoryObject(type=MemoryType.EXPERIENCE, content="x", project="dev",
                          learning_status=LearningStatus.OBSERVATION)
    svc.write_memory(lesson)
    with pytest.raises(GovernanceViolationError):
        svc.promote_lesson(lesson.memory_id, LearningStatus.SUPERSEDED, "evidence", promoted_by="Zak")
