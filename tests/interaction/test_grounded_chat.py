"""Chat is truthful: grounded in live facts, remembers the conversation, reports
model failures plainly, and never executes or fakes work (owner's Windows run:
invented security claims, a canned "6 tasks verified" report, a lost "yes")."""
from types import SimpleNamespace

from packages.contracts import ModelClass, ModelResponse, ModelUsage, ProviderName, TaskNode, TaskStatus
from services.core.dynamic_planner import PlanResultSynthesizer
from services.core.objective_analyzer import ObjectiveAnalyzer
from services.interaction.interaction_service import InteractionService
from services.policy.approval_service import ApprovalService


class Router:
    def __init__(self, text="ok", exc=None, mock=False, truncated=False):
        self.text, self.exc, self.mock, self.truncated = text, exc, mock, truncated
        self.requests = []
        self.providers = {}

    def invoke(self, req):
        self.requests.append(req)
        if self.exc:
            raise self.exc
        return ModelResponse(text=self.text, provider=ProviderName.GEMINI, model_name="stub",
                             usage=ModelUsage(), latency_ms=1, is_mock=self.mock, truncated=self.truncated)


def _chat(router):
    svc = InteractionService(commander=SimpleNamespace(model_router=router), approval_service=ApprovalService())
    svc.status_facts = lambda: ["Agent missions: 2 COMPLETED"]
    return svc


def test_prompt_is_grounded_in_live_facts_with_strong_model():
    router = Router()
    _chat(router).handle_text_input("What's your status?")
    req = router.requests[0]
    assert req.model_class == ModelClass.STANDARD and req.max_tokens >= 8192 and req.allow_partial
    assert "LIVE STATUS" in req.prompt and "Agent missions: 2 COMPLETED" in req.prompt
    assert "Level 3 (HOOD's own trained model): does not exist yet" in req.prompt
    for invented in ("Project Sentinel is", "self-healing", "vulnerability monitoring"):
        assert invented not in req.prompt


def test_model_failure_is_reported_not_papered_over():
    msg = _chat(Router(exc=RuntimeError("quota exhausted"))).handle_text_input("hello")
    assert "couldn't reach the AI model" in msg.text and "quota exhausted" in msg.text
    msg = _chat(Router(mock=True)).handle_text_input("hello")
    assert "No live AI model is connected" in msg.text


def test_long_reply_is_returned_and_marked_cut_off():
    msg = _chat(Router(text="part one", truncated=True)).handle_text_input("Explain everything")
    assert msg.text.startswith("part one") and "cut off" in msg.text


def test_build_request_offers_a_mission_and_never_starts_one():
    router = Router(text="Happy to help shape it.")
    svc = _chat(router)
    ask = "I want you to create a website for a coffee shop with a menu and QR ordering"
    reply = svc.handle_text_input(ask)
    assert reply.suggested_mission == ask
    again = svc.handle_text_input("yes")                    # follow-up re-offers the same plan
    assert again.suggested_mission == ask
    assert svc.approval_service.list_pending() == []        # nothing was started or approved
    assert _chat(Router()).handle_text_input("peux-tu créer un site pour mon café").suggested_mission
    assert _chat(Router()).handle_text_input("how are you?").suggested_mission is None
    later = "A client wants a website for his coffee shop with the menu. Can you create it?"
    assert _chat(Router()).handle_text_input(later).suggested_mission == later   # noun before verb
    assert _chat(Router()).handle_text_input("what is a website?").suggested_mission is None


def test_follow_up_carries_the_conversation():
    router = Router(text="Noted")
    svc = _chat(router)
    svc.handle_text_input("The client's shop is called Bean There.")
    svc.handle_text_input("What is the shop called?")
    assert "The client's shop is called Bean There." in router.requests[1].prompt


def test_cli_plan_report_states_only_what_happened():
    t1 = TaskNode(title="Review", objective="x", assigned_agent="Engineering_Lead", status=TaskStatus.COMPLETED)
    t1.response = SimpleNamespace(result={"analysis": "Looks reasonable.", "verdict": "UNVERIFIED"},
                                  tools_used=[], evidence=[])
    t2 = TaskNode(title="Check", objective="y", assigned_agent="Operations_Lead", status=TaskStatus.FAILED)
    out = PlanResultSynthesizer.synthesize(ObjectiveAnalyzer.analyze("Build a website"), {"a": t1, "b": t2})
    text = out["formatted_text"]
    assert "2 run, 1 finished, 1 failed" in text and "not" in text and "verified" in text
    assert "Looks reasonable." in text
    for canned in ("verified successfully", "All completed with verified evidence",
                   "Factual consensus", "unit economics verified", "Actions Requiring Approval: None"):
        assert canned not in text
