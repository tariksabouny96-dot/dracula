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
