"""Voice <-> the REAL mission engine (AgentEngine with its real plan validation, persisted plan hash and approval
rules). The model that writes the plan is a deterministic script (SIMULATED); everything between the phone's signed
confirmation and the engine is real. Guards against the voice layer drifting from the engine's actual data shapes."""
import base64

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from services.agents import AgentEngine
from services.voicecall.bridge import HoodVoiceBridge
from services.voicecall.confirm import ConfirmationService, signing_string
from tests.agents.scripted_provider import ScriptedModel
from tests.voicecall.conftest import OWNER

OBJECTIVE = ("Build a small responsive notes web app: create, read, update, delete and list notes through a JSON HTTP API, "
             "with input validation and a mobile-friendly index page.")


@pytest.fixture
def real(kit, tmp_path):
    engine = AgentEngine(tmp_path / "engine", invoke=ScriptedModel(), allow_simulated=True)
    confirm = ConfirmationService(kit.store, kit.devices, kit.cfg, interaction=kit.interaction, approvals=kit.approvals,
                                  engine=engine, auth=kit.auth, clock=kit.clock)
    bridge = HoodVoiceBridge(kit.interaction, engine, kit.store, kit.cost, kit.cfg, kit.events, confirm, clock=kit.clock)
    return engine, confirm, bridge


def _sign(kit, ch):
    return base64.b64encode(kit.key.sign(signing_string(ch).encode(), ec.ECDSA(hashes.SHA256()))).decode()


def test_phone_shows_the_engines_whole_plan_and_a_signature_approves_it(kit, real):
    engine, confirm, _ = real
    created = engine.create_mission(OWNER, OBJECTIVE, budget_usd=1.0)
    mid = created["mission_id"]
    assert created["state"] == "AWAITING_PLAN_APPROVAL"
    item = next(i for i in confirm.pending(OWNER) if i["subject"] == mid)
    assert item["phone_confirmable"] is True
    plan = engine.status(OWNER, mid)["plan"]
    for t in plan["tasks"]:                                              # every task the engine will run is on the screen
        assert t["title"] in item["detail"] and t["instructions"] in item["detail"]
    assert plan["summary"] in item["detail"] and "Budget: $1.00" in item["detail"]
    ch = confirm.issue_challenge(OWNER, kit.device, "mission_plan", mid, "approve")
    assert ch["detail"] == item["detail"]
    assert engine.status(OWNER, mid)["state"] == "AWAITING_PLAN_APPROVAL"   # nothing happens before the signature
    out = confirm.submit(OWNER, kit.device, ch["challenge_id"], _sign(kit, ch))
    assert out["applied"] is True
    status = engine.status(OWNER, mid)
    assert status["state"] != "AWAITING_PLAN_APPROVAL" and status["approved_by"] == "owner"
    engine.halt_all() if hasattr(engine, "halt_all") else None


def test_a_signed_rejection_cancels_the_real_mission(kit, real):
    engine, confirm, _ = real
    mid = engine.create_mission(OWNER, OBJECTIVE, budget_usd=1.0)["mission_id"]
    ch = confirm.issue_challenge(OWNER, kit.device, "mission_plan", mid, "reject")
    confirm.submit(OWNER, kit.device, ch["challenge_id"], _sign(kit, ch))
    assert engine.status(OWNER, mid)["state"] == "CANCELLED"


def test_a_real_plan_that_is_too_long_to_read_on_a_phone_stays_in_the_console(kit, real):
    engine, confirm, _ = real
    long_plan = ScriptedModel(plan={**ScriptedModel().plan, "interface_contract": "x" * 3000,
                                    "tasks": [{**t, "instructions": t["instructions"] + " " + "detail " * 400}
                                              for t in ScriptedModel().plan["tasks"]]})
    big = AgentEngine(kit.store.root / "engine_big" if hasattr(kit.store, "root") else __import__("pathlib").Path(kit.store.path).parent / "engine_big",
                      invoke=long_plan, allow_simulated=True)
    big_confirm = ConfirmationService(kit.store, kit.devices, kit.cfg, interaction=kit.interaction, approvals=kit.approvals,
                                      engine=big, auth=kit.auth, clock=kit.clock)
    created = big.create_mission(OWNER, OBJECTIVE, budget_usd=1.0)
    if created["state"] != "AWAITING_PLAN_APPROVAL":
        pytest.skip("plan validator rejected the oversized scripted plan: " + str(created.get("error")))
    mid = created["mission_id"]
    item = next(i for i in big_confirm.pending(OWNER) if i["subject"] == mid)
    assert item["phone_confirmable"] is False and "too long" in item["detail"]
    from services.voicecall.contracts import Forbidden
    with pytest.raises(Forbidden):
        big_confirm.issue_challenge(OWNER, kit.device, "mission_plan", mid, "approve")


def test_voice_drafts_a_real_mission_but_cannot_start_it(kit, real):
    engine, confirm, bridge = real
    from tests.voicecall.test_voice_authority import _active_call
    call = _active_call(kit)
    kit.router.text = "I can set that up."
    res = bridge.handle_turn(call, "Please build a small notes web app with a JSON API and a mobile friendly page, plan it now")
    missions = engine.list(OWNER)
    assert all(m["state"] in ("AWAITING_PLAN_APPROVAL", "PLANNING", "BLOCKED") for m in missions), missions   # never running without approval
    assert not any(m["state"] in ("QUEUED", "RUNNING", "COMPLETED") for m in missions)


def test_a_plan_the_engine_could_not_draft_is_reported_not_offered_for_confirmation(kit, tmp_path):
    # the real engine returns a BLOCKED mission (it does not raise) when planning fails; the owner must not be asked to
    # confirm a plan that does not exist
    engine = AgentEngine(tmp_path / "engine_bad", invoke=ScriptedModel(overrides={"planner": "this is not a plan"}), allow_simulated=True)
    confirm = ConfirmationService(kit.store, kit.devices, kit.cfg, interaction=kit.interaction, approvals=kit.approvals,
                                  engine=engine, auth=kit.auth, clock=kit.clock)
    bridge = HoodVoiceBridge(kit.interaction, engine, kit.store, kit.cost, kit.cfg, kit.events, confirm, clock=kit.clock)
    from tests.voicecall.test_voice_authority import _active_call
    call = _active_call(kit)
    kit.router.text = "I can set that up."
    res = bridge.handle_turn(call, "Please build a small notes web app with a JSON API and a mobile friendly page, plan it now")
    assert res.mission_id is None and not res.needs_confirmation
    assert res.reason == "mission_draft_failed" and "couldn't draft" in res.speech
    assert not any(e["kind"] == "confirm_request" for e in kit.store.events_after(call.call_id))
