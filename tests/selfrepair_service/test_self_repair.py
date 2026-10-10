"""Phase 4 self-repair: HOOD investigates a reported problem in its own code, proves a fix in the
sandbox (the new test fails before and passes after; the suite still passes), and changes its code
only with the Root Owner's approval of that exact fix (restore point, undo). When it isn't sure it
says so instead of guessing. The AI model is scripted; the fake HOOD repo has a planted bug."""
import base64
import json
from pathlib import Path

import pytest

from packages.contracts import ModelResponse, ModelUsage, ProviderName
from services.agents import sandbox as sandbox_mod
from services.selfrepair import SelfRepairConflict, SelfRepairService
from tests.agents.test_agent_engine import needs_netns

BUGGY = 'def greet(name):\n    """Greeting shown on the Command page."""\n    return "Helo, " + name\n'
FIXED_TEST = ('from services.demo.greeting import greet\n\n\ndef test_greeting_is_spelled_right():\n'
              '    assert greet("Zak") == "Hello, Zak"\n')
PASSING_TEST = 'def test_always_passes():\n    assert True\n'


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "hood"
    (root / "services" / "demo").mkdir(parents=True)
    (root / "services" / "demo" / "greeting.py").write_text(BUGGY)
    (root / "services" / "auth").mkdir()
    (root / "services" / "auth" / "auth_service.py").write_text("ROOT = 'zak'\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_existing.py").write_text("def test_existing():\n    assert 1 + 1 == 2\n")
    (root / "pytest.ini").write_text("[pytest]\n")
    return root


def proposal(**over):
    base = {"understood_problem": "The greeting on the Command page is misspelled ('Helo').",
            "root_cause": "services/demo/greeting.py returns 'Helo'.", "confidence": "high", "can_fix": True,
            "why_not": "", "edits": [{"path": "services/demo/greeting.py", "find": '"Helo, "', "replace": '"Hello, "'}],
            "test_path": "tests/selfrepair/test_greeting_spelling.py", "test_content": FIXED_TEST,
            "summary_for_owner": "The greeting said 'Helo'. It now says 'Hello'."}
    base.update(over)
    return base


class Model:
    """Scripted AI: answers the screenshot question with text, fix requests with the queued proposals."""

    def __init__(self, *proposals):
        self.queue = list(proposals)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        text = "The Command page shows the greeting 'Helo, Zak'." if request.images else json.dumps(self.queue.pop(0))
        return ModelResponse(text=text, provider=ProviderName.GEMINI, model_name="gemini-test", latency_ms=1,
                             usage=ModelUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20))


def service(tmp_path, repo, model, **kw):
    return SelfRepairService(tmp_path / "data", repo_root=repo, invoke=model, synchronous=True,
                             suite_args=["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], **kw)


REPORT = 'On the Command page the greeting says "Helo" instead of Hello. Please fix the greeting.'


@needs_netns
def test_a_clear_bug_is_fixed_in_the_sandbox_then_applied_only_with_the_owners_ok(tmp_path, repo):
    svc = service(tmp_path, repo, Model(proposal()))
    rid = svc.report("owner", REPORT)["id"]
    r = svc.get(rid)
    assert r["state"] == "NEEDS_DECISION", (r["error"], r["checks"], r["log"])
    assert [c["name"] for c in r["checks"]] == ["reproduces_the_problem", "fix_makes_it_pass", "whole_suite_still_passes"]
    assert all(c["passed"] for c in r["checks"])
    assert "-    return \"Helo, \" + name" in r["diff"] and (repo / "services/demo/greeting.py").read_text() == BUGGY
    with pytest.raises(PermissionError):
        svc.apply(rid, r["proposal_sha"], "admin2", is_root_owner=False)
    with pytest.raises(SelfRepairConflict, match="doesn't match"):
        svc.apply(rid, "0" * 64, "owner", is_root_owner=True)
    applied = svc.apply(rid, r["proposal_sha"], "owner", is_root_owner=True)
    assert applied["state"] == "APPLIED" and applied["needs_restart"]
    assert '"Hello, "' in (repo / "services/demo/greeting.py").read_text()
    assert (repo / "tests/selfrepair/test_greeting_spelling.py").read_text() == FIXED_TEST
    assert Path(applied["applied"]["patch"]).read_text().startswith("--- a/services/demo/greeting.py")
    undone = svc.undo(rid, "owner", is_root_owner=True)
    assert undone["state"] == "UNDONE" and (repo / "services/demo/greeting.py").read_text() == BUGGY
    assert not (repo / "tests/selfrepair/test_greeting_spelling.py").exists()


def test_without_a_sandbox_the_owner_decides_whether_checks_run_on_this_pc(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "sandbox_problem", lambda *a: "no sandbox on this computer")
    allowed = {"on": False}
    svc = service(tmp_path, repo, Model(proposal()), local_run_allowed=lambda: allowed["on"])
    rid = svc.report("owner", REPORT)["id"]
    assert svc.get(rid)["state"] == "AWAITING_LOCAL_RUN"
    assert (repo / "services/demo/greeting.py").read_text() == BUGGY
    with pytest.raises(SelfRepairConflict, match="turned off"):
        svc.approve_local_run(rid, "owner")
    allowed["on"] = True
    r = svc.approve_local_run(rid, "owner")
    assert r["state"] == "NEEDS_DECISION", (r["error"], r["checks"])
    assert all(c["passed"] for c in r["checks"])


def test_when_hood_is_not_sure_it_says_so_and_changes_nothing(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "sandbox_problem", lambda *a: "no sandbox")
    cases = [proposal(can_fix=False, edits=[], why_not="The cause is a network setting, not code."),
             proposal(confidence="low")]
    for case in cases:
        svc = service(tmp_path / case["confidence"] / str(case["can_fix"]), repo, Model(case))
        r = svc.get(svc.report("owner", REPORT)["id"])
        assert r["state"] == "NO_RELIABLE_FIX" and r["diagnosis"]["why_not"]
    assert (repo / "services/demo/greeting.py").read_text() == BUGGY


def test_protected_or_invalid_fixes_are_refused_after_one_correction(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "sandbox_problem", lambda *a: "no sandbox")
    bad = proposal(edits=[{"path": "services/auth/auth_service.py", "find": "'zak'", "replace": "'evil'"}])
    model = Model(bad, bad)
    svc = service(tmp_path, repo, model)
    r = svc.get(svc.report("owner", REPORT)["id"])
    assert r["state"] == "NO_RELIABLE_FIX" and "protected" in r["diagnosis"]["why_not"]
    assert len([q for q in model.requests if q.response_schema]) == 2     # one correction asked for
    assert (repo / "services/auth/auth_service.py").read_text() == "ROOT = 'zak'\n"
    for edit, why in [({"path": "../outside.py", "find": "a", "replace": "b"}, "editable path"),
                      ({"path": "services/demo/greeting.py", "find": "nope", "replace": "x"}, "appears 0 times"),
                      ({"path": "tests/test_existing.py", "find": "2", "replace": "3"}, "protected")]:
        with pytest.raises(Exception, match=why):
            svc.check_proposal(proposal(edits=[edit]))
    with pytest.raises(Exception, match="tests/selfrepair"):
        svc.check_proposal(proposal(test_path="tests/test_existing.py"))


@needs_netns
def test_a_test_that_does_not_reproduce_the_problem_is_not_proof(tmp_path, repo):
    weak = proposal(test_content=PASSING_TEST)
    svc = service(tmp_path, repo, Model(weak, weak))
    r = svc.get(svc.report("owner", REPORT)["id"])
    assert r["state"] == "NO_RELIABLE_FIX" and "doesn't prove" in r["diagnosis"]["why_not"]


@needs_netns
def test_a_fix_that_breaks_the_suite_is_not_proposed(tmp_path, repo):
    (repo / "tests" / "test_existing.py").write_text(
        "from services.demo.greeting import greet\n\ndef test_existing():\n    assert greet('a').startswith('Helo')\n")
    svc = service(tmp_path, repo, Model(proposal(), proposal()))
    r = svc.get(svc.report("owner", REPORT)["id"])
    assert r["state"] == "NO_RELIABLE_FIX" and "breaks something else" in r["diagnosis"]["why_not"]


def test_screenshots_need_consent_and_are_read_by_the_model(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(sandbox_mod, "sandbox_problem", lambda *a: "no sandbox")
    model = Model(proposal())
    svc = service(tmp_path, repo, model)
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()
    with pytest.raises(ValueError, match="Confirm"):
        svc.report("owner", REPORT, png, "image/png", screenshot_consent=False)
    with pytest.raises(ValueError, match="PNG, JPEG or WebP"):
        svc.report("owner", REPORT, png, "image/gif", screenshot_consent=True)
    r = svc.report("owner", REPORT, png, "image/png", screenshot_consent=True)
    assert r["has_screenshot"] and "Helo" in svc.get(r["id"])["screenshot_note"]
    assert model.requests[0].images[0]["mime_type"] == "image/png"
    assert svc.screenshot(r["id"]) == (base64.b64decode(png), "image/png")


def test_the_right_files_are_found_from_the_owners_words(tmp_path, repo):
    svc = service(tmp_path, repo, Model())
    assert svc.relevant_files(REPORT)[0] == "services/demo/greeting.py"
    assert svc.relevant_files("something completely unrelated xyzzy") == []
    with pytest.raises(ValueError):
        svc.report("owner", "short")
