"""Website missions are verified by reading files only, so they complete on hosts with no
sandbox (the owner's Windows PC); Python missions there wait for the owner's "Run on my PC"
approval, bound to the exact files, instead of ending UNVERIFIED."""
import io
import tempfile
from pathlib import Path
import json
import sqlite3
import zipfile

import pytest

from services.agents import AgentEngine, MissionConflict
from services.agents import engine as engine_mod
from services.agents.contracts import AgentWorkProduct, FileWrite
from services.agents.sandbox import Workspace
from services.agents.specialists import check_acceptance_spec
from services.agents.static_web import js_problems, load_spec, SpecError, verify_static_site
from tests.agents.scripted_provider import ScriptedModel
from tests.agents.test_agent_engine import needs_netns
from tests.agents.scripted_web import ACCEPTANCE, WEB_OBJECTIVE, ScriptedWebModel, site_files

OWNER = "user_root_owner_01"
NO_SANDBOX = "Process sandbox is only implemented for POSIX hosts"


def _engine(tmp_path, model):
    return AgentEngine(tmp_path / "engine", invoke=model, allow_simulated=True)


def _approved(engine, objective, profile):
    created = engine.create_mission(OWNER, objective, budget_usd=1.0, profile=profile)
    assert created["state"] == "AWAITING_PLAN_APPROVAL", created["error"]
    return engine.approve_plan(OWNER, created["mission_id"], created["plan_sha256"], approver="owner")


def test_website_mission_catches_planted_defect_repairs_and_delivers_without_running_code(tmp_path, monkeypatch):
    def no_processes(*a, **k):
        raise AssertionError("a website mission must never run a process")
    monkeypatch.setattr(Workspace, "run", no_processes)
    monkeypatch.setattr(engine_mod, "sandbox_problem", lambda *a: NO_SANDBOX)   # as on Windows
    model = ScriptedWebModel()
    engine = _engine(tmp_path, model)
    mid = _approved(engine, WEB_OBJECTIVE, "static_web")["mission_id"]
    final = engine.run(OWNER, mid)
    assert final["state"] == "COMPLETED", final["error"]
    assert final["profile"] == "static_web" and final["repairs"] == 1
    assert final["preview_path"].startswith(f"/preview/{mid}/")
    v = final["last_verification"]
    assert v["execution"] == "none: files read, nothing run" and v["verifier"] == "deterministic-static-checks"

    with sqlite3.connect(engine.db_path) as db:
        bodies = [json.loads(b) for (b,) in db.execute("SELECT body FROM receipts WHERE kind='VERIFICATION' "
                                                       "ORDER BY created")]
        approval = json.loads(db.execute("SELECT body FROM receipts WHERE kind='PLAN_APPROVAL'").fetchone()[0])
    assert [b["verdict"] for b in bodies] == ["FAIL", "PASS"]
    first = {c["name"]: c["passed"] for c in bodies[0]["checks"]}
    assert first["links_and_assets"] is False and first["independent_acceptance_checks"] is False
    assert approval["scope"]["commands"] == [] and approval["scope"]["role_write_roots"]["engineer"] == ["site"]
    # The repair prompt carried the verifier's report (broken link and the missing product).
    repair = [r for r in model.requests if r.agent == "engineer"][-1].prompt
    assert "orders.html does not exist" in repair and "menu_cappuccino" in repair

    name, data, _ = engine.artifact(OWNER, mid)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "site/index.html" in names and "qa_checks/acceptance.json" in names
    kinds = [e["kind"] for e in engine.events(OWNER, mid)]
    assert "TASK_STARTED" in kinds and "VERIFIED" in kinds


def test_website_roles_write_only_their_own_folders(tmp_path):
    model = ScriptedWebModel(overrides={"engineer": json.dumps({"files": [
        {"path": "app/server.py", "content": "print('hi')\n"}], "notes": "", "uncertainty": ""})})
    engine = _engine(tmp_path, model)
    mid = _approved(engine, WEB_OBJECTIVE, "static_web")["mission_id"]
    final = engine.run(OWNER, mid)
    assert final["state"] == "BLOCKED" and "may not write app/server.py" in final["error"]
    assert not (engine.root / "workspaces" / mid / "app").exists()
    # Every retry told the engineer what was wrong.
    retries = [r for r in model.requests if r.agent == "engineer"][1:]
    assert retries and all("YOUR PREVIOUS ANSWER WAS REJECTED" in r.prompt and "app/server.py" in r.prompt
                           for r in retries)


def test_malformed_acceptance_spec_is_rejected_before_it_is_written():
    bad = AgentWorkProduct(files=[FileWrite(path="qa_checks/acceptance.json",
                                            content=json.dumps({"checks": [{"type": "magic", "page": "x.html"}]}))])
    with pytest.raises(ValueError, match="type is .magic.; it must be one of"):
        check_acceptance_spec(bad)
    with pytest.raises(SpecError, match="Unsupported selector"):
        load_spec(json.dumps({"checks": [{"type": "has_element", "page": "index.html", "selector": "a:hover"}]}))
    assert len(load_spec(json.dumps(ACCEPTANCE))) == len(ACCEPTANCE["checks"])


def _site(tmp_path, files, spec=ACCEPTANCE):
    ws = Workspace(tmp_path / "ws", require_network_isolation=False)
    for f in files:
        path = ws.root / f["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f["content"], encoding="utf-8")
    if spec is not None:
        (ws.root / "qa_checks").mkdir(exist_ok=True)
        (ws.root / "qa_checks" / "acceptance.json").write_text(json.dumps(spec), encoding="utf-8")
    return ws


def test_static_checks_find_real_defects(tmp_path):
    files = site_files(fixed=True)
    files[0]["content"] = files[0]["content"].replace("<!DOCTYPE html>\n", "").replace(
        "</nav>", '</nav><script src="https://cdn.example.com/x.js"></script><a href="#nowhere">x</a>')
    files[4]["content"] += "\nfunction broken() { if (x) { return 1; }\n"
    decision = verify_static_site(_site(tmp_path, files))
    report = {c.name: c for c in decision.checks}
    assert decision.verdict.value == "FAIL"
    assert "missing <!DOCTYPE html>" in report["site_structure"].output_tail
    assert "from the internet" in report["links_and_assets"].output_tail
    assert "#nowhere" in report["links_and_assets"].output_tail
    assert "is never closed" in report["javascript_structure"].output_tail
    assert report["independent_acceptance_checks"].passed     # the content itself is right


def test_good_site_passes_and_says_nothing_was_run(tmp_path):
    decision = verify_static_site(_site(tmp_path, site_files(fixed=True)))
    assert decision.verdict.value == "PASS", [c.output_tail for c in decision.checks if not c.passed]
    assert "not run" in decision.reason
    acceptance = decision.checks[-1]
    assert acceptance.tests_collected == len(ACCEPTANCE["checks"]) and "8 passed, 0 failed" in acceptance.output_tail


def test_missing_or_broken_acceptance_spec_never_passes(tmp_path):
    assert verify_static_site(_site(tmp_path / "a", site_files(True), spec=None)).verdict.value == "UNVERIFIED"
    broken = verify_static_site(_site(tmp_path / "b", site_files(True), spec={"checks": "nope"}))
    assert broken.verdict.value == "FAIL" and broken.checks[-1].suite_invalid
    assert verify_static_site(_site(tmp_path / "c", [], spec=None)).verdict.value == "UNVERIFIED"


@pytest.mark.parametrize("code", [
    "const a = `x ${b + `y ${c}`} z`; const r = /a[/]b/g.test(s); if (x) { y = a / b / c; }",
    "function f(){ return /ab+c/i; }\nlet s = 'it\\'s';\n// ) comment\n/* } */",
    "x = a ? /re/ : b; arr = [/a/, /b/]; y = (1) / 2; const t = `<li>${i.name}</li>`;",
])
def test_javascript_structure_check_accepts_valid_code(code):
    assert js_problems(code) == []


@pytest.mark.parametrize("code,needle", [
    ("function f() { return 1;", "never closed"), ("const s = 'abc;\nfoo()", "string"),
    ("let a = [1, 2);", "closes '['"), ("x = `abc", "template"), ("if (a) { b(); }}", "no matching"),
])
def test_javascript_structure_check_finds_breakage(code, needle):
    assert needle in " ".join(js_problems(code))


def test_preview_is_capability_scoped_and_confined(tmp_path):
    engine = _engine(tmp_path, ScriptedWebModel())
    mid = _approved(engine, WEB_OBJECTIVE, "static_web")["mission_id"]
    status = engine.run(OWNER, mid)
    token = status["preview_path"].split("/")[3]
    data, mime = engine.preview_file(mid, token, "index.html")
    assert b"Bean There" in data and mime.startswith("text/html")
    assert engine.preview_file(mid, token, "js/order.js")[1].startswith("text/javascript")
    for bad_token, path in (("0" * 40, "index.html"), (token, "../../.receipt_key"), (token, "../qa_checks/acceptance.json")):
        with pytest.raises(KeyError):
            engine.preview_file(mid, bad_token, path)
    files = engine.files(OWNER, mid)
    by_path = {f["path"]: f for f in files["files"]}
    assert by_path["site/index.html"]["written_by"]["role"] == "engineer"
    assert by_path["qa_checks/acceptance.json"]["written_by"]["role"] == "qa"
    assert "Bean There" in engine.read_file(OWNER, mid, "site/index.html")["text"]
    with pytest.raises(KeyError):
        engine.read_file(OWNER, mid, "../.receipt_key")
    with pytest.raises(KeyError):
        engine.files("intruder", mid)


# ------------------------------------------------------------------ "Run on my PC"
def test_python_mission_without_sandbox_waits_for_owner_then_runs_on_this_pc(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "sandbox_problem", lambda *a: NO_SANDBOX)
    engine = _engine(tmp_path, ScriptedModel())
    objective = ("Build a small responsive notes web app: create, read, update, delete and list notes "
                 "through a JSON HTTP API, with input validation and a mobile-friendly index page.")
    mid = _approved(engine, objective, "python_app")["mission_id"]
    waiting = engine.run(OWNER, mid)
    assert waiting["state"] == "BLOCKED" and waiting["local_run"]["awaiting_approval"]
    assert "Run on my PC" in waiting["error"] and NO_SANDBOX in waiting["error"]
    sha = waiting["local_run"]["workspace_sha256"]

    with pytest.raises(MissionConflict, match="turned off"):          # off by default
        engine.approve_local_run(OWNER, mid, sha, "owner")
    assert engine.set_local_run(True, "owner")["allow_local_run"] is True
    with pytest.raises(MissionConflict, match="changed"):
        engine.approve_local_run(OWNER, mid, "0" * 64, "owner")
    with pytest.raises(KeyError):
        engine.approve_local_run("intruder", mid, sha, "intruder")

    # First run on this PC finds the planted bug; the repair changes the files, so it asks again.
    assert engine.approve_local_run(OWNER, mid, sha, "owner")["state"] == "VERIFYING"
    again = engine.run(OWNER, mid)
    assert again["state"] == "BLOCKED" and again["repairs"] == 1
    assert again["local_run"]["workspace_sha256"] != sha
    engine.approve_local_run(OWNER, mid, again["local_run"]["workspace_sha256"], "owner")
    final = engine.run(OWNER, mid)
    assert final["state"] == "COMPLETED", final["error"]
    assert final["last_verification"]["execution"] == "owner-approved run on this computer (no isolation)"
    assert final["last_verification"]["network_isolated"] is False
    with sqlite3.connect(engine.db_path) as db:
        approvals = [json.loads(b) for (b,) in db.execute("SELECT body FROM receipts WHERE kind='LOCAL_RUN_APPROVAL'")]
    assert len(approvals) == 2 and approvals[0]["isolation"].startswith("none")
    assert engine.verify_receipts(OWNER, mid)["valid"]


@needs_netns              # the resumed check really runs in the Linux sandbox
def test_python_mission_waiting_for_the_sandbox_continues_by_itself_when_it_is_ready(tmp_path, monkeypatch):
    """Windows: the owner approves HOOD's Linux sandbox once; the mission doesn't need another click."""
    problem = {"now": "HOOD is setting up its Linux sandbox right now."}
    monkeypatch.setattr(engine_mod, "sandbox_problem", lambda *a: problem["now"])
    engine = _engine(tmp_path, ScriptedModel())
    objective = ("Build a small responsive notes web app: create, read, update, delete and list notes "
                 "through a JSON HTTP API, with input validation and a mobile-friendly index page.")
    mid = _approved(engine, objective, "python_app")["mission_id"]
    waiting = engine.run(OWNER, mid)
    assert waiting["state"] == "BLOCKED" and "continues by itself" in waiting["error"]
    assert engine.resume_waiting() == []                          # sandbox not ready: nothing runs
    problem["now"] = None                                         # HOOD finished setting it up
    engine.continue_runner = lambda owner, mission_id: None
    assert engine.resume_waiting() == [(OWNER, mid)]
    final = engine.run(OWNER, mid)
    assert final["state"] == "COMPLETED", final["error"]
    assert final["last_verification"]["execution"] == "sandbox"
    with sqlite3.connect(engine.db_path) as db:
        kinds = [k for (k,) in db.execute("SELECT kind FROM receipts WHERE mission_id=?", (mid,))]
    assert "ENVIRONMENT_READY" in kinds and "LOCAL_RUN_APPROVAL" not in kinds
    assert engine.verify_receipts(OWNER, mid)["valid"]


def test_local_run_settings_are_off_by_default_and_persist(tmp_path):
    engine = _engine(tmp_path, ScriptedModel())
    assert engine.local_run_settings()["allow_local_run"] is False
    engine.set_local_run(True, "owner")
    reopened = _engine(tmp_path, ScriptedModel())
    assert reopened.local_run_settings()["allow_local_run"] is True
    assert reopened.local_run_settings()["changed_by"] == "owner"
    with pytest.raises(ValueError):
        engine.set_local_run("yes", "owner")


def test_unknown_mission_kind_is_refused(tmp_path):
    with pytest.raises(ValueError, match="python_app, static_web or wordpress_site"):
        _engine(tmp_path, ScriptedModel()).create_mission(OWNER, WEB_OBJECTIVE, profile="rocket")


def test_qa_mistake_is_fed_back_and_fixed_instead_of_failing_the_mission(tmp_path):
    """Owner's run: 'check 4: page must be a page path relative to site/' three times -> FAILED."""
    bad = {"checks": [dict(c) for c in ACCEPTANCE["checks"]]}
    bad["checks"][3]["page"] = "style.css"                                  # not a page: must be rejected
    bad["checks"][0]["page"] = "site/index.html"                            # harmless variant: accepted
    bad["checks"][1]["page"] = "/index.html#top"
    tries = {"qa": 0}

    def qa(request):
        tries["qa"] += 1
        spec = ACCEPTANCE if "YOUR PREVIOUS ANSWER WAS REJECTED" in request.prompt else bad
        if tries["qa"] > 1:
            assert "'page' is 'style.css'" in request.prompt                # told exactly what was wrong
        return json.dumps({"files": [{"path": "qa_checks/acceptance.json", "content": json.dumps(spec)}],
                           "notes": "", "uncertainty": ""})
    engine = _engine(tmp_path, ScriptedWebModel(overrides={"qa": qa}))
    final = engine.run(OWNER, _approved(engine, WEB_OBJECTIVE, "static_web")["mission_id"])
    assert final["state"] == "COMPLETED", final["error"]
    assert tries["qa"] == 2


def test_tolerant_page_names_and_nested_selectors_in_acceptance_checks(tmp_path):
    spec = {"checks": [
        {"id": "a", "type": "links_to", "page": "/", "target": "site/menu.html"},
        {"id": "b", "type": "has_element", "page": "menu", "selector": "ul#menu > li.item", "min_count": "3"},
        {"id": "c", "type": "has_element", "page": "./order.html?table=5", "selector": "form#order button#add"},
        {"id": "d", "type": "contains_text", "page": "menu.html#drinks", "text": "Cappuccino 3.20"}]}
    decision = verify_static_site(_site(tmp_path, site_files(fixed=True), spec=spec))
    assert decision.verdict.value == "PASS", decision.checks[-1].output_tail


def test_blocked_agent_gets_a_fresh_round_when_the_owner_retries(tmp_path):
    always_bad = json.dumps({"files": [{"path": "qa_checks/acceptance.json", "content": "{not json"}],
                             "notes": "", "uncertainty": ""})
    model = ScriptedWebModel(overrides={"qa": always_bad})
    engine = _engine(tmp_path, model)
    mid = _approved(engine, WEB_OBJECTIVE, "static_web")["mission_id"]
    blocked = engine.run(OWNER, mid)
    assert blocked["state"] == "BLOCKED" and "QA agent's answer was rejected 3 times" in blocked["error"]
    assert "Retry blocked work" in blocked["error"] and "[" not in blocked["error"].split("(")[0]
    model.overrides.pop("qa")                                               # the model gets it right now
    engine.retry_blocked(OWNER, mid, "owner")
    assert engine.run(OWNER, mid)["state"] == "COMPLETED"


def test_planner_is_told_what_cannot_be_built_and_the_owner_sees_it():
    model = ScriptedWebModel()
    engine = _engine(Path(tempfile.mkdtemp()), model)
    status = engine.create_mission(OWNER, "Create a website using WordPress for perfumes, with a catalogue",
                                   profile="static_web")
    # A website mission asked for WordPress: HOOD says it's the static option and how to get real WordPress.
    assert any("not WordPress" in n and "WordPress site" in n for n in status["scope_notes"])
    planner_prompt = next(r for r in model.requests if r.agent == "planner").prompt
    assert "HOOD SCOPE NOTES" in planner_prompt and "WordPress" in planner_prompt
    plain = engine.create_mission(OWNER, WEB_OBJECTIVE, profile="static_web")
    assert plain["scope_notes"] == []
