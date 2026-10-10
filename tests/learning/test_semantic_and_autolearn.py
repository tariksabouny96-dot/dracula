"""Combined A+B: semantic recall over learned lessons, and the agent engine
auto-recording a lesson from every finished mission."""
import re
import pytest

from packages.contracts import LearningStatus
from services.memory.service import MemoryService
from services.learning.service import LearningService
from services.learning.embeddings import cosine
from tests.agents.test_agent_engine import make_engine, approved, OWNER, needs_netns, ScriptedModel


class FakeEmbedder:
    """Deterministic 2-D embedder: dim0 = networking concepts, dim1 = date concepts.
    Semantically-related texts share a dimension even with no words in common."""
    name = "fake"
    NET = {"network", "networks", "connection", "connections", "timeout", "timeouts",
           "retry", "retries", "socket", "outage", "problems", "503"}
    DATE = {"date", "dates", "format", "formats", "utc", "timezone", "calendar", "time"}

    def embed(self, texts):
        out = []
        for t in texts:
            toks = set(re.findall(r"\w+", t.lower()))
            out.append([float(len(toks & self.NET)), float(len(toks & self.DATE))])
        return out


def test_cosine_basic():
    assert cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)


def test_semantic_recall_ranks_by_meaning_not_keywords(tmp_path):
    mem = MemoryService(db_path=tmp_path / "mem.db")
    svc = LearningService(mem, data_dir=tmp_path / "learn", embedder=FakeEmbedder())
    svc.record_outcome("alice", "net", "retry on connection timeout")   # networking, no query words
    svc.record_outcome("alice", "dt", "format dates in utc")            # dates
    # Query shares NO words with either lesson, but is semantically networking.
    hits = svc.recall("alice", query="network outage", min_status=LearningStatus.OBSERVATION)
    assert hits and hits[0]["ranking"] == "semantic"
    assert "connection timeout" in hits[0]["content"]       # networking lesson ranked first
    assert hits[0]["score"] > hits[-1]["score"]


def test_lexical_fallback_is_labeled(tmp_path):
    mem = MemoryService(db_path=tmp_path / "mem.db")
    svc = LearningService(mem, data_dir=tmp_path / "learn")  # no embedder
    svc.record_outcome("alice", "net", "retry on connection timeout")
    hits = svc.recall("alice", query="connection", min_status=LearningStatus.OBSERVATION)
    assert hits and all(h["ranking"] == "lexical" for h in hits)


@needs_netns
def test_finished_mission_auto_records_a_lesson(tmp_path):
    mem = MemoryService(db_path=tmp_path / "mem.db")
    learn = LearningService(mem, data_dir=tmp_path / "learn")

    def hook(owner, mission_id, objective, outcome, detail):
        learn.record_outcome(owner, f"mission:{outcome}", f"{outcome}: {objective[:80]}",
                             evidence=f"mission {mission_id}")

    engine = make_engine(tmp_path, model=ScriptedModel(), on_outcome=hook)
    mid = approved(engine)["mission_id"]
    engine.run(OWNER, mid)
    lessons = learn.list(OWNER)
    assert len(lessons) >= 1
    assert lessons[0]["category"].startswith("mission:")
