"""
HOOD v0.1 Integration Tests - Real Persistent Memory
Verifies cross-restart persistence, semantic search via token similarity,
and JSON migration export/import.
"""

import pytest
from pathlib import Path
from packages.contracts import MemoryObject, MemoryType, LearningStatus
from services.memory.service import MemoryService


def test_memory_persistence_across_reinstantiation(tmp_path):
    """Simulates process restart by closing and re-opening the SQLite database."""
    db_file = tmp_path / "hood_test_memory.db"

    # Process Session 1: Store memories
    svc1 = MemoryService(db_path=db_file)
    mem1 = MemoryObject(
        project="integration_test",
        type=MemoryType.EPISODIC,
        content="Action: build_core completed successfully by operator Zak",
        source="session_run_01"
    )
    svc1.write_memory(mem1)

    mem2 = MemoryObject(
        project="integration_test",
        type=MemoryType.SEMANTIC,
        content="Antigravity build instructions for HOOD autonomous run",
        source="spec_v1.1"
    )
    svc1.write_memory(mem2)

    # Process Session 2: Fresh instance pointing to the same file
    svc2 = MemoryService(db_path=db_file)
    project_memories = svc2.query_memories("integration_test")
    assert len(project_memories) == 2

    # Query specific memory
    retrieved = svc2.get_memory(mem1.memory_id)
    assert retrieved is not None
    assert "build_core" in retrieved.content
    assert retrieved.source == "session_run_01"


def test_semantic_memory_search(tmp_path):
    """Tests semantic search ranking using token similarity."""
    db_file = tmp_path / "hood_semantic.db"
    svc = MemoryService(db_path=db_file)

    svc.write_memory(MemoryObject(
        project="ai_system",
        type=MemoryType.SEMANTIC,
        content="Gemini API adapter handles fast model routing to Google Gemini API."
    ))
    svc.write_memory(MemoryObject(
        project="ai_system",
        type=MemoryType.SEMANTIC,
        content="PostgreSQL schema defines pgvector tables for long-term production storage."
    ))

    results = svc.query_lexical("Gemini Google API routing", project="ai_system", top_k=2)
    assert len(results) > 0
    top_result = results[0]
    assert "Gemini" in top_result.content


def test_memory_migration_export_import(tmp_path):
    """Verifies that memories can be exported to JSON and imported into a new environment."""
    source_db = tmp_path / "source.db"
    dest_db = tmp_path / "dest.db"
    export_json = tmp_path / "memories_export.json"

    # Populate source
    src_svc = MemoryService(db_path=source_db)
    src_svc.write_memory(MemoryObject(
        project="export_test",
        type=MemoryType.PROCEDURAL,
        content="Procedure: backup_vault, steps: encrypt, store"
    ))
    src_svc.write_memory(MemoryObject(
        project="export_test",
        type=MemoryType.DECISION,
        content="Decision: Use SQLite on dev laptop, rationale: Lightweight"
    ))

    # Export
    exported_count = src_svc.export_memories(export_json)
    assert exported_count == 2
    assert export_json.exists()

    # Import into clean destination
    dest_svc = MemoryService(db_path=dest_db)
    imported_count = dest_svc.import_memories(export_json)
    assert imported_count == 2

    dest_memories = dest_svc.query_memories("export_test")
    assert len(dest_memories) == 2
    assert any("backup_vault" in m.content for m in dest_memories)
