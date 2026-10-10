"""WordPress missions: the agents write a theme and the site's pages; HOOD builds a real
WordPress (on SQLite) from the owner-approved toolbox, renders every page in the sandbox and
judges the rendered HTML with the same independent checks as website missions.

Workspace (written by agents):  theme/   the WordPress theme (style.css header, PHP templates)
                                 content/site.json   site title, pages, front page, menu
                                 qa_checks/acceptance.json   QA's checks (page = page slug)
Runtime (written by HOOD only):  <engine>/runtime/<mission>/  WordPress copy, SQLite DB, rendered pages
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .contracts import CheckResult, VerificationDecision, VerificationVerdict
from .static_web import (SITE_ROOT, SPEC_PATH, Page, SpecError, _check, _run_spec, css_problems, js_problems,
                         load_spec)

THEME_ROOT = "theme"
CONTENT_PATH = "content/site.json"
THEME_SLUG = "hood-theme"
HARNESS = str(Path(__file__).with_name("wp_harness.py"))
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,60}$")

ROUTER_PHP = """<?php
// HOOD: router for PHP's built-in server so WordPress pretty links work.
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path !== '/' && strpos($path, '..') === false && is_file(__DIR__ . $path)) {
    return false;
}
$_SERVER['SCRIPT_NAME'] = '/index.php';
require __DIR__ . '/index.php';
"""


class ContentError(ValueError):
    """content/site.json is missing or malformed: the engineer must fix it."""


def load_content(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContentError(f"{CONTENT_PATH} is not valid JSON: {exc.msg} (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ContentError(f"{CONTENT_PATH} must be a JSON object")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ContentError(f'{CONTENT_PATH} needs a non-empty "pages" list')
    seen = set()
    for n, page in enumerate(pages, 1):
        if not isinstance(page, dict):
            raise ContentError(f"page {n} is not an object")
        slug = str(page.get("slug", "")).strip().strip("/").lower()
        if not SLUG_RE.fullmatch(slug):
            raise ContentError(f"page {n}: slug {page.get('slug')!r} must be lowercase letters, digits and dashes")
        if slug in seen:
            raise ContentError(f"page {n}: duplicate slug {slug!r}")
        seen.add(slug)
        if not str(page.get("title", "")).strip():
            raise ContentError(f"page {slug}: needs a title")
        if not isinstance(page.get("content", ""), str):
            raise ContentError(f"page {slug}: content must be HTML text")
        page["slug"] = slug
    front = str(data.get("front_page", "")).strip().strip("/")
    if front and front not in seen:
        raise ContentError(f"front_page {front!r} is not one of the pages")
    menu = data.get("menu")
    if menu is not None and (not isinstance(menu, list) or any(str(s).strip("/") not in seen for s in menu)):
        raise ContentError('"menu" must list page slugs')
    return data


def check_theme_files(files: Dict[str, str]) -> None:
    """Pre-check the engineer's output before it is written (so a broken answer is retried)."""
    style = files.get(f"{THEME_ROOT}/style.css")
    if style is None or not re.search(r"Theme Name\s*:", style):
        raise ContentError("theme/style.css with a 'Theme Name:' header is required")
    if f"{THEME_ROOT}/index.php" not in files:
        raise ContentError("theme/index.php is required (WordPress won't activate a theme without it)")
    if CONTENT_PATH in files:
        load_content(files[CONTENT_PATH])


def spec_key(page: str) -> str:
    """QA names WordPress pages by slug: '/', 'index', 'home' (front page), 'catalogue', '/catalogue/'."""
    p = str(page or "").strip().split("#", 1)[0].split("?", 1)[0].strip("/")
    p = re.sub(r"\.html?$", "", p)
    p = re.sub(r"(^|/)index$", "", p).strip("/")
    return p or "index"


def prepare_runtime(runtime: Path, workspace_root: Path, toolbox: Any) -> Dict[str, str]:
    """Fresh WordPress for this verification: core (copied once), SQLite drop-in, config, theme."""
    runtime.mkdir(parents=True, exist_ok=True)
    wp = runtime / "wordpress"
    core = toolbox.path("wordpress")
    version_file = core / "wp-includes" / "version.php"
    marker = runtime / "core.version"
    current = version_file.read_text(errors="replace") if version_file.is_file() else ""
    if not wp.is_dir() or not marker.is_file() or marker.read_text() != current:
        shutil.rmtree(wp, ignore_errors=True)
        shutil.copytree(core, wp, symlinks=False)
        marker.write_text(current)
    plugin_src = toolbox.path("wp_sqlite")
    plugin_dest = wp / "wp-content" / "plugins" / "sqlite-database-integration"
    shutil.rmtree(plugin_dest, ignore_errors=True)
    shutil.copytree(plugin_src, plugin_dest)
    dropin = (plugin_dest / "db.copy").read_text(encoding="utf-8")
    dropin = dropin.replace("{SQLITE_IMPLEMENTATION_FOLDER_PATH}", str(plugin_dest)).replace(
        "{SQLITE_PLUGIN}", "sqlite-database-integration/load.php")
    (wp / "wp-content" / "db.php").write_text(dropin, encoding="utf-8")
    db_dir = runtime / "db"
    shutil.rmtree(db_dir, ignore_errors=True)                  # every verification starts from an empty site
    db_dir.mkdir()
    salts = "\n".join(f"define('{k}', '{secrets.token_hex(32)}');" for k in (
        "AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY", "AUTH_SALT", "SECURE_AUTH_SALT",
        "LOGGED_IN_SALT", "NONCE_SALT"))
    (wp / "wp-config.php").write_text(f"""<?php
// Written by HOOD for one verification run. SQLite database, no network, no file changes.
define('DB_NAME', 'wordpress'); define('DB_USER', ''); define('DB_PASSWORD', ''); define('DB_HOST', '');
define('DB_CHARSET', 'utf8'); define('DB_COLLATE', '');
define('DB_DIR', {json.dumps(str(db_dir) + "/")}); define('DB_FILE', 'site.sqlite');
{salts}
$table_prefix = 'wp_';
define('WP_DEBUG', true); define('WP_DEBUG_DISPLAY', true); define('WP_DEBUG_LOG', false);
define('WP_HTTP_BLOCK_EXTERNAL', true); define('AUTOMATIC_UPDATER_DISABLED', true);
define('DISABLE_WP_CRON', true); define('DISALLOW_FILE_MODS', true); define('WP_ENVIRONMENT_TYPE', 'local');
if (!defined('ABSPATH')) {{ define('ABSPATH', __DIR__ . '/'); }}
require_once ABSPATH . 'wp-settings.php';
""", encoding="utf-8")
    (wp / "hood-router.php").write_text(ROUTER_PHP, encoding="utf-8")
    theme_dest = wp / "wp-content" / "themes" / THEME_SLUG
    shutil.rmtree(theme_dest, ignore_errors=True)
    shutil.copytree(workspace_root / THEME_ROOT, theme_dest)
    shutil.rmtree(runtime / "rendered", ignore_errors=True)
    (runtime / "rendered").mkdir()
    return {"runtime": str(runtime), "wordpress": str(wp)}


def harness_argv(runtime: Path, toolbox: Any, workspace_root: Path, port: int) -> List[str]:
    return [sys.executable, "-I", "-B", HARNESS, "--runtime", str(runtime), "--wpcli", str(toolbox.path("wp_cli")),
            "--content", str(workspace_root / CONTENT_PATH), "--port", str(port)]


def _link_resolver(base: str):
    """Classify links in rendered WordPress HTML for the acceptance checks (slug-keyed pages)."""
    from urllib.parse import urljoin, urlsplit
    host = urlsplit(base).netloc

    def resolve(page_rel: str, ref: str):
        ref = (ref or "").strip()
        if ref.startswith("#"):
            return "fragment", page_rel, ref[1:] or None
        full = urlsplit(urljoin(base + "/", ref))
        if full.scheme in ("mailto", "tel", "javascript", "data"):
            return "special", None, full.scheme
        if full.netloc != host:
            return "external", None, None
        key = spec_key(full.path)
        return "local", f"{SITE_ROOT}/{key}.html", full.fragment or None
    return resolve


def judge(workspace, runtime: Path, run_result: Optional[CheckResult]) -> VerificationDecision:
    """Turn the harness report and rendered pages into HOOD's independent verdict."""
    digest = workspace.digest()
    report_path = runtime / "rendered" / "report.json"
    checks: List[CheckResult] = []
    if run_result is not None and not report_path.is_file():
        checks.append(_check("wordpress_setup", [f"The WordPress run did not finish (exit {run_result.exit_code}): "
                                                 + run_result.output_tail[-600:]]))
        return VerificationDecision(verdict=VerificationVerdict.FAIL, checks=checks, workspace_sha256=digest,
                                    reason="Failed checks: wordpress_setup")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    checks.append(_check("php_syntax", report.get("lint", [])))
    setup = report.get("setup", {})
    setup_problems = list(setup.get("errors", []))
    if not setup.get("ok") and not setup_problems:
        setup_problems = ["WordPress was not set up because the theme has PHP errors" if report.get("lint")
                          else "WordPress was not set up"]
    checks.append(_check("wordpress_setup", setup_problems))
    pages = report.get("pages", [])
    render = []
    for p in pages:
        if p.get("status") != 200:
            render.append(f"{p.get('path')}: HTTP {p.get('status')}")
        for err in p.get("php_errors", []):
            render.append(f"{p.get('path')}: PHP {err}")
    if setup.get("ok") and not pages:
        render.append("No page could be fetched from the running site")
    checks.append(_check("pages_render", render, [f"{len(pages)} page(s) fetched from the running site"]
                         if pages else None))

    rendered: Dict[str, Page] = {}
    for p in pages:
        f = runtime / "rendered" / "pages" / (p["key"] + ".html")
        if p.get("status") == 200 and f.is_file():
            rendered[f"{SITE_ROOT}/{p['key']}.html"] = Page(f"{SITE_ROOT}/{p['key']}.html",
                                                              f.read_text(encoding="utf-8", errors="replace"))
    structure = []
    for key, page in sorted(rendered.items()):
        name = key[len(SITE_ROOT) + 1:-5] or "index"
        label = "/" if name == "index" else f"/{name}/"
        if not page.doctype.lower().startswith("doctype html"):
            structure.append(f"{label}: missing <!DOCTYPE html> (theme header)")
        title = page.first("title")
        if title is None or not title.text:
            structure.append(f"{label}: missing <title> (call wp_head() and add_theme_support('title-tag'))")
        metas = [el for el in page.elements if el.tag == "meta"]
        if not any(m.attrs.get("name", "").lower() == "viewport" for m in metas):
            structure.append(f'{label}: missing <meta name="viewport"> (needed on phones)')
        dupes = [i for i, count in page.ids().items() if count > 1]
        if dupes:
            structure.append(f"{label}: duplicate id(s) {', '.join(sorted(dupes)[:5])}")
    checks.append(_check("site_structure", structure))

    theme_files = [p for p in workspace.listing(limit=10_000) if p.startswith(THEME_ROOT + "/")]
    css, js = [], []
    for rel in theme_files:
        text = (workspace.root / rel).read_text(encoding="utf-8", errors="replace")
        if rel.endswith(".css"):
            css += [f"{rel}: {e}" for e in css_problems(text)]
        elif rel.endswith(".js"):
            js += [f"{rel}: {e}" for e in js_problems(text)]
    checks.append(_check("css_syntax", css))
    checks.append(_check("javascript_structure", js))

    collected = 0
    spec_file = workspace.root / SPEC_PATH
    if not spec_file.is_file():
        checks.append(_check("independent_acceptance_checks", [f"{SPEC_PATH} was not written"], collected=0))
    else:
        try:
            spec = load_spec(spec_file.read_text(encoding="utf-8", errors="replace"))
        except SpecError as exc:
            checks.append(_check("independent_acceptance_checks", [str(exc)], collected=0, suite_invalid=True))
        else:
            for chk in spec:     # page names are slugs here: "catalogue" -> the /catalogue/ page
                chk["page"] = spec_key(chk["page"]) + ".html"
                if chk.get("type") == "links_to":
                    page_part, _, frag = str(chk["target"]).partition("#")
                    chk["target"] = (spec_key(page_part) + ".html" if page_part else "") + ("#" + frag if frag else "")
            lines, failed = _run_spec(spec, rendered, resolver=_link_resolver(report.get("base", "")))
            collected = len(spec)
            result = _check("independent_acceptance_checks", [ln for ln in lines if ln.startswith("FAIL")],
                            [ln for ln in lines if ln.startswith("PASS")], collected=collected)
            result.output_tail = (f"{collected - failed} passed, {failed} failed\n" + result.output_tail)[-4000:]
            checks.append(result)

    if workspace.digest() != digest:
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=checks,
                                    workspace_sha256=workspace.digest(), reason="Workspace changed during verification")
    acceptance = checks[-1]
    if acceptance.suite_invalid:
        return VerificationDecision(verdict=VerificationVerdict.FAIL, checks=checks, workspace_sha256=digest,
                                    reason="The QA acceptance checks are malformed")
    if not collected:
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=checks, workspace_sha256=digest,
                                    reason="No independent acceptance checks were written")
    failed = [c.name for c in checks if not c.passed]
    if failed:
        return VerificationDecision(verdict=VerificationVerdict.FAIL, checks=checks, workspace_sha256=digest,
                                    reason="Failed checks: " + ", ".join(failed))
    return VerificationDecision(verdict=VerificationVerdict.PASS, checks=checks, workspace_sha256=digest,
                                reason=f"WordPress ran the theme and pages; {len(pages)} page(s) rendered without "
                                       f"errors and {collected} acceptance checks passed")


INSTALL_MD = """# Install this WordPress theme and its pages

Built by HOOD's agents and checked on a real WordPress ({wp}) running locally.

1. Copy the folder `theme/` into your site's `wp-content/themes/`, rename it (e.g. `{slug}`),
   then activate it in WordPress: Appearance > Themes.
2. Create the pages listed in `content/site.json` (Pages > Add New: same title, slug and content),
   or with WP-CLI from your WordPress folder:

{commands}

3. Settings > Reading: "A static page", Homepage = `{front}`.

`preview/` holds the pages exactly as WordPress rendered them during HOOD's check.
"""


def install_guide(workspace_root: Path, wp_version: str) -> str:
    try:
        content = load_content((workspace_root / CONTENT_PATH).read_text(encoding="utf-8"))
    except (OSError, ContentError):
        content = {"pages": []}
    cmds = []
    for p in content.get("pages", []):
        cmds.append("   wp post create --post_type=page --post_status=publish "
                    f"--post_title={json.dumps(p['title'])} --post_name={p['slug']} --post_content=\"$(cat <<'HTML'\n"
                    f"{p.get('content', '')}\nHTML\n)\"")
    return INSTALL_MD.format(wp=wp_version or "WordPress", slug=THEME_SLUG, commands="\n".join(cmds) or "   (no pages)",
                             front=content.get("front_page") or "(your choice)")
