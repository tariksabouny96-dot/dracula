"""Chat knows what happened in recent missions, not just their counts (owner's run:
HOOD said "no work was executed" when the agents had written code that couldn't be tested)."""
import sqlite3
from datetime import datetime, timezone


def test_recent_mission_details_reach_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path))
    from hood_cli import HoodSystemRuntime
    rt = HoodSystemRuntime()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(rt.agent_engine.db_path) as db:
        db.execute("INSERT INTO missions (id, owner, objective, state, budget_usd, error, created, updated, "
                   "schema_version) VALUES (?,?,?,?,?,?,?,?,?)",
                   ("agm_1", "owner", "Coffee shop website with QR ordering", "UNVERIFIED", 1.0,
                    "Process sandbox is only implemented for POSIX hosts", now, now, 1))
        for i, (role, state) in enumerate([("qa", "COMPLETED"), ("engineer", "COMPLETED"), ("reviewer", "COMPLETED")]):
            db.execute("INSERT INTO tasks (mission_id, task_id, seq, role, title, instructions, depends_on, state, "
                       "updated) VALUES (?,?,?,?,?,?,?,?,?)", ("agm_1", f"t{i}", i, role, role, "x", "[]", state, now))
    facts = "\n".join(rt._chat_status_facts())
    assert 'Recent mission "Coffee shop website with QR ordering": UNVERIFIED; 3/3 agent tasks finished' in facts
    assert "the agents wrote the code, but it could not be tested yet" in facts
