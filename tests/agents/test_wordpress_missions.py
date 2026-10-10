"""WordPress missions: tools need the owner's OK first; the agents' theme and pages are run by
WordPress in the sandbox and the rendered HTML is judged independently.

WordPress itself is NOT executed here: the harness run is replaced by a fake that writes what the
real harness writes (report.json + rendered pages), so these tests cover HOOD's logic. The real
run needs PHP + WordPress installed through the toolbox (owner's WSL2, or an approved live test).
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

from packages.contracts import ModelResponse, ModelUsage, ProviderName
from services.agents import AgentEngine, MissionConflict
from services.agents import engine as engine_mod
from services.agents import wp_harness
from services.agents.contracts import AgentWorkProduct, FileWrite
from services.agents.sandbox import Workspace
from services.agents.specialists import check_wordpress_work
from services.agents.wordpress import ContentError, load_content, prepare_runtime, spec_key

OWNER = "user_root_owner_01"
OBJECTIVE = ("A WordPress website for the Maison Noir perfume shop: a home page and a catalogue page listing "
             "Rose Noir 120 EUR, Amber Oasis 140 EUR and Vanilla Dreams 110 EUR, with a menu.")
PLAN = {"summary": "Classic WordPress theme plus two pages.", "deliverable": "theme/ and content/site.json",
        "interface_contract": "Pages: home (front), catalogue. Catalogue: div.product with names and prices.",
        "tasks": [{"id": "build_theme", "role": "engineer", "title": "Build the theme and pages",
                   "instructions": "Write theme/ and content/site.json.", "depends_on": []},
                  {"id": "acceptance", "role": "qa", "title": "Write acceptance checks",
                   "instructions": "Write qa_checks/acceptance.json.", "depends_on": []}],
        "clarifications_needed": []}
STYLE = "/*\nTheme Name: Maison Noir\n*/\nbody { font-family: serif; }\n"
SITE = {"site_title": "Maison Noir", "tagline": "Perfumes", "front_page": "home", "menu": ["home", "catalogue"],
        "pages": [{"slug": "home", "title": "Home", "content": "<h1>Maison Noir</h1><p>Welcome.</p>"},
                  {"slug": "catalogue", "title": "Catalogue", "content":
                   "<div class='product'>Rose Noir 120 EUR</div><div class='product'>Amber Oasis 140 EUR</div>"
                   "<div class='product'>Vanilla Dreams 110 EUR</div>"}]}
CHECKS = {"checks": [
    {"id": "home", "type": "page_exists", "page": "/"},
    {"id": "brand", "type": "contains_text", "page": "index", "text": "Maison Noir"},
    {"id": "three_products", "type": "has_element", "page": "catalogue", "selector": "div.product", "min_count": 3},
    {"id": "rose", "type": "contains_text", "page": "/catalogue/", "text": "Rose Noir 120 EUR"},
    {"id": "menu_to_catalogue", "type": "links_to", "page": "/", "target": "catalogue"}]}


def theme_files(buggy):
    functions = "<?php\nadd_theme_support('title-tag');\n" + ("// BUG: undefined variable\n" if buggy else "")
    return [{"path": "theme/style.css", "content": STYLE}, {"path": "theme/index.php", "content": "<?php get_header();"},
            {"path": "theme/functions.php", "content": functions},
            {"path": "content/site.json", "content": json.dumps(SITE)}]


class Model:
    def __init__(self):
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        role = request.agent
        if role == "planner":
            text = json.dumps(PLAN)
        elif role == "engineer":
            repairing = "INDEPENDENT VERIFIER FAILURE REPORT" in request.prompt
            text = json.dumps({"files": theme_files(buggy=not repairing), "notes": "", "uncertainty": ""})
        elif role == "qa":
            text = json.dumps({"files": [{"path": "qa_checks/acceptance.json", "content": json.dumps(CHECKS)}],
                               "notes": "", "uncertainty": ""})
        else:
            text = json.dumps({"findings": [], "notes": ""})
        return ModelResponse(text=text, provider=ProviderName.MOCK, model_name="scripted-wp", usage=ModelUsage(),
                             latency_ms=1, is_mock=True)


class FakeToolbox:
    def __init__(self, root: Path, ready=True, approved=False):
        self.root, self.ready, self.approved_, self.ensured = root, ready, approved, []
        (root / "wp" / "wp-includes").mkdir(parents=True)
        (root / "wp" / "wp-includes" / "version.php").write_text("<?php $wp_version = '7.1.3';")
        (root / "wp" / "index.php").write_text("<?php")
        (root / "plugin").mkdir()
        (root / "plugin" / "db.copy").write_text("<?php $p = '{SQLITE_IMPLEMENTATION_FOLDER_PATH}'; // {SQLITE_PLUGIN}")
        (root / "wp-cli.phar").write_text("phar")

    def path(self, tool):
        return {"wordpress": self.root / "wp", "wp_sqlite": self.root / "plugin", "wp_cli": self.root / "wp-cli.phar"}[tool]

    def needs_for_profile(self, profile):
        missing = [] if self.ready else ["php", "wordpress", "wp_sqlite", "wp_cli"]
        names = {"php": "PHP", "wordpress": "WordPress", "wp_sqlite": "WordPress SQLite plugin", "wp_cli": "WP-CLI"}
        return {"tools": list(names), "missing": missing, "unapproved": [] if self.approved_ else missing,
                "names": names, "ready": self.ready, "problem": None, "running": None}

    def ensure(self, ids, actor="hood"):
        self.ensured.append(ids)

    def detect(self, tool):
        return {"installed": True, "version": "7.1.3"}


def fake_wordpress_run(original):
    """Stand-in for the sandboxed harness: renders the agents' pages the way WordPress would."""
    def run(self, name, argv, timeout=120, *, unisolated=False):
        if name != "wordpress_render":
            return original(self, name, argv, timeout, unisolated=unisolated)
        runtime = Path(argv[argv.index("--runtime") + 1])
        site = json.loads((self.root / "content" / "site.json").read_text())
        buggy = "BUG" in (self.root / "theme" / "functions.php").read_text()
        base = "http://127.0.0.1:8080"
        menu = "".join(f'<a href="{base}/{s}/">{s}</a>' for s in site["menu"])
        pages = []
        for page in site["pages"]:
            key = "index" if page["slug"] == site["front_page"] else page["slug"]
            warning = "<b>Warning</b>: Undefined variable $x in functions.php on line 3<br>" if buggy else ""
            html = (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
                    f"content='width=device-width, initial-scale=1'><title>{page['title']} – {site['site_title']}"
                    f"</title><link rel='stylesheet' href='{base}/wp-content/themes/hood-theme/style.css?ver=1'>"
                    f"</head><body>{warning}<nav>{menu}</nav><main>{page['content']}</main></body></html>")
            out = runtime / "rendered" / "pages" / f"{key}.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html)
            preview = runtime / "rendered" / "preview" / f"{key}.html"
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_text(html.replace(base + "/", ""))
            pages.append({"path": "/" if key == "index" else f"/{key}/", "key": key, "status": 200,
                          "php_errors": wp_harness.php_errors(html), "bytes": len(html)})
        report = {"base": base, "lint": [], "setup": {"ok": True, "errors": [], "steps": []}, "pages": pages}
        (runtime / "rendered" / "report.json").write_text(json.dumps(report))
        from services.agents.contracts import CheckResult
        return CheckResult(name=name, command=argv, exit_code=0, passed=True, output_tail="")
    return run


@pytest.fixture
def wp_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_mod, "sandbox_problem", lambda *a: None)
    monkeypatch.setattr(Workspace, "run", fake_wordpress_run(Workspace.run))
    model = Model()
    toolbox = FakeToolbox(tmp_path / "tools")
    engine = AgentEngine(tmp_path / "engine", invoke=model, allow_simulated=True, toolbox=toolbox)
    return engine, toolbox, model


def test_tools_need_the_owners_ok_before_the_plan_can_be_approved(wp_engine):
    engine, toolbox, _ = wp_engine
    toolbox.ready = False
    created = engine.create_mission(OWNER, OBJECTIVE, profile="wordpress_site")
    assert created["needs_tools"]["unapproved"] and not created["needs_tools"]["ready"]
    assert any("Needs your OK to install" in n for n in created["scope_notes"])
    with pytest.raises(MissionConflict, match="Needs your OK to install PHP, WordPress"):
        engine.approve_plan(OWNER, created["mission_id"], created["plan_sha256"], "owner")
    toolbox.approved_ = True                                    # allowed earlier: HOOD installs without asking
    with pytest.raises(MissionConflict, match="Installing"):
        engine.approve_plan(OWNER, created["mission_id"], created["plan_sha256"], "owner")
    toolbox.ready = True
    assert engine.approve_plan(OWNER, created["mission_id"], created["plan_sha256"], "owner")["state"] == "QUEUED"


def test_wordpress_mission_is_rendered_checked_repaired_and_delivered(wp_engine):
    engine, toolbox, model = wp_engine
    created = engine.create_mission(OWNER, OBJECTIVE, profile="wordpress_site")
    assert "WordPress site" in next(r for r in model.requests if r.agent == "planner").system_prompt
    mid = engine.approve_plan(OWNER, created["mission_id"], created["plan_sha256"], "owner")["mission_id"]
    final = engine.run(OWNER, mid)
    assert final["state"] == "COMPLETED", final["error"]
    assert final["repairs"] == 1                                   # the PHP warning was caught and fixed
    v = final["last_verification"]
    names = {c["name"]: c["passed"] for c in v["checks"]}
    assert {"php_syntax", "wordpress_setup", "pages_render", "site_structure",
            "independent_acceptance_checks"} <= set(names) and all(names.values())
    repair = [r for r in model.requests if r.agent == "engineer"][-1].prompt
    assert "Warning: Undefined variable" in repair
    _, data, _ = engine.artifact(OWNER, mid)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert {"theme/style.css", "content/site.json", "INSTALL.md", "preview/index.html"} <= set(names)
    token = final["preview_path"].split("/")[3]
    assert b"Maison Noir" in engine.preview_file(mid, token, "index.html")[0]
    assert b"Rose Noir" in engine.preview_file(mid, token, "catalogue.html")[0]


def test_engineer_output_without_a_real_theme_is_sent_back():
    work = AgentWorkProduct(files=[FileWrite(path="theme/style.css", content="body{}"),
                                   FileWrite(path="theme/index.php", content="<?php")])
    with pytest.raises(ValueError, match="Theme Name"):
        check_wordpress_work(work, {})
    ok = AgentWorkProduct(files=[FileWrite(path="theme/header.php", content="<?php")])   # a repair round
    check_wordpress_work(ok, {"theme/style.css": STYLE, "theme/index.php": "<?php"})
    with pytest.raises(ContentError, match="slug"):
        load_content(json.dumps({"pages": [{"slug": "Bad Slug!", "title": "x"}]}))
    with pytest.raises(ContentError, match="front_page"):
        load_content(json.dumps({"front_page": "nope", "pages": [{"slug": "home", "title": "Home"}]}))


def test_runtime_is_a_fresh_sqlite_wordpress_outside_the_workspace(tmp_path):
    toolbox = FakeToolbox(tmp_path / "tools")
    ws = tmp_path / "ws"
    (ws / "theme").mkdir(parents=True)
    (ws / "theme" / "style.css").write_text(STYLE)
    runtime = tmp_path / "runtime"
    prepare_runtime(runtime, ws, toolbox)
    wp = runtime / "wordpress"
    config = (wp / "wp-config.php").read_text()
    assert "DB_DIR" in config and str(runtime / "db") in config and "WP_HTTP_BLOCK_EXTERNAL" in config
    assert "DISALLOW_FILE_MODS" in config and "put your unique phrase" not in config
    dropin = (wp / "wp-content" / "db.php").read_text()
    assert "{SQLITE" not in dropin and "sqlite-database-integration/load.php" in dropin
    assert (wp / "wp-content" / "themes" / "hood-theme" / "style.css").read_text() == STYLE
    assert (wp / "hood-router.php").is_file()
    (runtime / "db" / "site.sqlite").write_text("old data")
    prepare_runtime(runtime, ws, toolbox)
    assert not (runtime / "db" / "site.sqlite").exists()           # every check starts from an empty site


def test_harness_helpers():
    assert wp_harness.page_key("/") == "index" and wp_harness.page_key("/shop/rose/") == "shop/rose"
    assert wp_harness.php_errors("<b>Fatal error</b>: Uncaught Error: x in /a.php:3") == \
        ["Fatal error: Uncaught Error: x in /a.php:3"]
    assert wp_harness.php_errors("<p>Everything fine</p>") == []
    html = ('<a href="http://127.0.0.1:8080/catalogue/">C</a><a href="/about">A</a>'
            '<a href="http://127.0.0.1:8080/wp-admin/">admin</a><a href="http://127.0.0.1:8080/feed/">rss</a>'
            '<a href="https://example.com/">ext</a><link href="http://127.0.0.1:8080/wp-includes/x.css">')
    assert wp_harness.internal_paths(html, "http://127.0.0.1:8080") == ["/catalogue/", "/about/"]
    for name in ("/", "index", "index.html", "home.html"):
        assert spec_key(name) in ("index", "home")
    assert spec_key("/catalogue/") == spec_key("catalogue") == spec_key("catalogue/index.html") == "catalogue"


def test_preview_copy_rewrites_links_and_copies_theme_assets(tmp_path):
    wp = tmp_path / "wordpress"
    css = wp / "wp-content" / "themes" / "hood-theme" / "style.css"
    css.parent.mkdir(parents=True)
    css.write_text("body{background:url(img/bg.png)}")
    (css.parent / "img").mkdir()
    (css.parent / "img" / "bg.png").write_bytes(b"png")
    preview = tmp_path / "preview"
    html = ('<link rel="stylesheet" href="http://127.0.0.1:8080/wp-content/themes/hood-theme/style.css?ver=1">'
            '<a href="http://127.0.0.1:8080/">Home</a><a href="http://127.0.0.1:8080/shop/rose/">Rose</a>'
            '<a href="http://127.0.0.1:8080/wp-login.php">login</a>')
    out = wp_harness.rewrite_for_preview(html, "http://127.0.0.1:8080", "catalogue", {"index", "shop/rose"},
                                         str(wp), str(preview))
    assert 'href="wp-content/themes/hood-theme/style.css"' in out
    assert 'href="index.html"' in out and 'href="shop/rose.html"' in out and 'href="#"' in out
    assert (preview / "wp-content" / "themes" / "hood-theme" / "img" / "bg.png").read_bytes() == b"png"
    nested = wp_harness.rewrite_for_preview(html, "http://127.0.0.1:8080", "shop/rose", {"index", "shop/rose"},
                                            str(wp), str(preview))
    assert 'href="../index.html"' in nested
