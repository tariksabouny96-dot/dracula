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


def test_prompt_is_grounded_in_live_facts_with_fast_model():
    router = Router()
    _chat(router).handle_text_input("What's your status?")
    req = router.requests[0]
    # Fast tier: chat must answer in seconds (the standard tier took ~2 minutes per reply).
    assert req.model_class == ModelClass.FAST and req.max_tokens >= 8192 and req.allow_partial
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


def test_mission_brief_is_drafted_from_the_whole_conversation():
    """Owner's run: the plan dialog was filled with "Yeah, sure... can we proceed?" instead of the spec."""
    router = Router(text="Sounds good.")
    svc = _chat(router)
    sid = "user:owner"
    svc.handle_text_input("A client wants a website for his coffee shop with the menu and products.", session_id=sid)
    svc.handle_text_input("Customers scan a QR code to order from their table.", session_id=sid)
    svc.handle_text_input("Yeah, sure. Can we proceed and create the website?", session_id=sid)
    router.text = "Build a local coffee-shop website. Features: menu, products, QR ordering."
    out = svc.draft_mission_objective(sid)
    assert out["drafted"] and out["objective"].startswith("Build a local coffee-shop website")
    prompt = router.requests[-1].prompt
    for said in ("menu and products", "QR code to order", "Use only what the owner said"):
        assert said in prompt


def test_mission_brief_falls_back_to_the_owners_own_words():
    svc = _chat(Router(text="ok"))
    sid = "user:owner"
    svc.handle_text_input("Build a menu page for the cafe.", session_id=sid)
    svc.commander.model_router = Router(exc=RuntimeError("quota exhausted"))
    out = svc.draft_mission_objective(sid)
    assert not out["drafted"] and "Build a menu page for the cafe." in out["objective"]
    assert "quota exhausted" in out["note"]


def _with_memory(tmp_path, router):
    from services.memory.service import MemoryService
    svc = InteractionService(commander=SimpleNamespace(model_router=router), approval_service=ApprovalService(),
                             memory_service=MemoryService(db_path=tmp_path / "mem.db"))
    svc.status_facts = lambda: []
    return svc


def test_chat_replies_are_not_saved_as_memories(tmp_path):
    """Owner's run: Memory showed "Zak: ... | Hood: <invented answer>" cards at 0.95 confidence."""
    svc = _with_memory(tmp_path, Router(text="Your firewall is fully secure."))
    svc.handle_text_input("How secure am I?", session_id="user:owner")
    stored = svc.memory_service.query_memories(project="conversation") + \
        svc.memory_service.query_memories(project="personal")
    assert stored == []


def test_remember_command_saves_the_owners_words_and_grounds_later_answers(tmp_path):
    router = Router(text="Noted.")
    svc = _with_memory(tmp_path, router)
    reply = svc.handle_text_input("Remember that the coffee shop is called Bean There.", session_id="user:owner")
    assert "Saved to memory" in reply.text and router.requests == []          # no model call needed
    facts = [m.content for m in svc.memory_service.query_memories(project="personal")]
    assert facts == ["The coffee shop is called Bean There"]
    svc.handle_text_input("souviens-toi que le café ouvre à 7h", session_id="user:owner")
    assert len(svc.memory_service.query_memories(project="personal")) == 2
    assert "Saved" not in svc.handle_text_input("Remember when we talked?", session_id="user:owner").text
    svc.handle_text_input("What should the homepage headline say?", session_id="user:owner")
    assert "The coffee shop is called Bean There" in router.requests[-1].prompt


def test_owner_asking_to_plan_opens_the_plan_and_button_stays_available():
    """Owner's run: HOOD said 'press the button below' with no button, then 'I can't create missions'."""
    router = Router(text="Sure.")
    svc = _chat(router)
    sid = "user:owner"
    first = svc.handle_text_input("Can you create a website for my perfume shop with a catalogue?", session_id=sid)
    assert first.suggested_mission and not first.open_mission_draft
    assert "button IS shown" in router.requests[-1].prompt
    eta = svc.handle_text_input("Plan this as a mission and tell me its ETA", session_id=sid)
    assert eta.suggested_mission == first.suggested_mission and eta.open_mission_draft
    assert "opening the mission plan" in router.requests[-1].prompt
    later = svc.handle_text_input("so did you start?", session_id=sid)
    assert later.suggested_mission == first.suggested_mission and not later.open_mission_draft
    off_topic = svc.handle_text_input("What's the capital of France?", session_id=sid)
    assert off_topic.suggested_mission is None
    assert "No mission button is shown" in router.requests[-1].prompt
    assert svc.approval_service.list_pending() == []                       # still nothing started


def test_wordpress_request_gets_honest_limits_before_planning():
    router = Router(text="Here's what I can do.")
    svc = _chat(router)
    reply = svc.handle_text_input("Build a WordPress website for perfumes with a catalogue and checkout")
    assert any("WordPress can't be built" in n for n in reply.scope_notes)
    assert any("No real payments" in n for n in reply.scope_notes)
    prompt = router.requests[-1].prompt
    assert "LIMITS FOR THIS REQUEST" in prompt and "cannot install or run WordPress" in prompt
    svc.handle_text_input("Build a WordPress site", session_id="user:o")
    router.text = "Not possible here: WordPress. Build a static catalogue site."
    svc.draft_mission_objective("user:o")
    assert "Not possible here" in router.requests[-1].prompt


def test_planned_request_is_not_offered_again():
    router = Router(text="Sure.")
    svc = _chat(router)
    sid = "user:owner"
    svc.handle_text_input("Create a website for my bakery", session_id=sid)
    svc.mission_planned(sid)                                    # the owner planned it from the dialog
    after = svc.handle_text_input("how long does a website mission take here?", session_id=sid)
    assert after.suggested_mission is None
    assert "No mission button is shown" in router.requests[-1].prompt


def test_limits_ignore_things_the_brief_rules_out():
    from services.agents.scope import scope_notes
    assert scope_notes("Constraints:\n- No WordPress, PHP, databases, or npm packages\n- No real payments") == []
    assert scope_notes("Use WordPress with a MySQL database")
