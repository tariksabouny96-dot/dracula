"""WordPress mission harness: runs INSIDE the sandbox (stdlib only, started by the engine).

    python -I -B wp_harness.py --runtime DIR --wpcli PHAR --content site.json --port 8080

1. lints every theme PHP file (php -l);
2. installs WordPress on SQLite with WP-CLI, activates the agents' theme, creates their pages,
   sets the front page and pretty links, and a menu for themes that register one;
3. serves the site with PHP's built-in server on loopback and fetches every internal page;
4. writes the rendered HTML (for HOOD's checks), a static preview copy, and report.json.

Everything it writes goes under the runtime directory (never the mission workspace), and the
child processes get HOME/TMPDIR/WP-CLI cache there too, so the files being judged can't change.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

THEME_SLUG = "hood-theme"
MAX_PAGES = 40
PHP_ERROR = re.compile(r"(?:<b>)?(Fatal error|Parse error|Warning|Notice|Deprecated|Uncaught \w+)(?:</b>)?:\s*(.{0,200})",
                       re.IGNORECASE)
SKIP_PATH = re.compile(r"^/(wp-admin|wp-login\.php|wp-json|xmlrpc\.php|feed|comments/feed|wp-cron\.php|"
                       r"wp-content|wp-includes)(/|$)|^/\?|/feed/?$")
LINK = re.compile(r"""(?P<attr>href|src)\s*=\s*(?P<q>["'])(?P<url>[^"']+)(?P=q)""", re.IGNORECASE)
CSS_URL = re.compile(r"""url\(\s*(['"]?)(?P<url>[^'")]+)\1\s*\)""", re.IGNORECASE)


def page_key(path: str) -> str:
    """'/' -> 'index', '/catalogue/' -> 'catalogue', '/shop/rose/' -> 'shop/rose'."""
    clean = urllib.parse.urlsplit(path).path.strip("/")
    return clean or "index"


def php_errors(html: str) -> List[str]:
    found = []
    for m in PHP_ERROR.finditer(html or ""):
        text = re.sub(r"<[^>]+>", "", m.group(0)).strip()
        if text not in found:
            found.append(text[:240])
    return found[:10]


def internal_paths(html: str, base: str) -> List[str]:
    """Same-site page paths linked from this HTML (no admin, feeds, assets, query-only links)."""
    out = []
    host = urllib.parse.urlsplit(base).netloc
    for m in LINK.finditer(html or ""):
        if m.group("attr").lower() != "href":
            continue
        url = urllib.parse.urljoin(base + "/", m.group("url").strip())
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != host or parts.scheme not in ("http", "https"):
            continue
        path = parts.path or "/"
        if SKIP_PATH.search(path) or re.search(r"\.(css|js|png|jpe?g|gif|svg|webp|ico|xml|txt|woff2?)$", path, re.I):
            continue
        if not path.endswith("/") and "." not in posixpath.basename(path):
            path += "/"
        if path not in out:
            out.append(path)
    return out


def rewrite_for_preview(html: str, base: str, key: str, keys: set, wp_root: str, preview_dir: str) -> str:
    """Make a rendered page browsable as static files: page links -> relative .html files,
    WordPress/theme assets -> copied next to the preview. Anything else on this host -> '#'."""
    host = urllib.parse.urlsplit(base).netloc
    here = posixpath.dirname(key + ".html")

    def rel(target: str) -> str:
        return posixpath.relpath(target, here or ".")

    def fix(url: str) -> str:
        full = urllib.parse.urljoin(base + "/", url)
        parts = urllib.parse.urlsplit(full)
        if parts.netloc != host:
            return url
        path = parts.path or "/"
        frag = ("#" + parts.fragment) if parts.fragment else ""
        if path.startswith(("/wp-content/", "/wp-includes/")):
            src = os.path.normpath(os.path.join(wp_root, path.lstrip("/")))
            if src.startswith(os.path.normpath(wp_root) + os.sep) and os.path.isfile(src):
                dest = os.path.join(preview_dir, path.lstrip("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if not os.path.exists(dest):
                    shutil.copyfile(src, dest)
                    if dest.endswith(".css"):
                        _copy_css_assets(src, dest, wp_root, preview_dir)
                return rel(path.lstrip("/")) + frag
            return "#"
        k = page_key(path)
        if k in keys:
            return rel(k + ".html") + frag
        return "#"

    def sub(m: "re.Match") -> str:
        return f'{m.group("attr")}={m.group("q")}{fix(m.group("url"))}{m.group("q")}'
    return LINK.sub(sub, html)


def _copy_css_assets(src_css: str, dest_css: str, wp_root: str, preview_dir: str) -> None:
    """Fonts and images a copied stylesheet refers to (relative url(...))."""
    try:
        text = open(src_css, encoding="utf-8", errors="replace").read()
    except OSError:
        return
    for m in CSS_URL.finditer(text):
        url = m.group("url").strip()
        if url.startswith(("data:", "http:", "https:", "//", "#")):
            continue
        src = os.path.normpath(os.path.join(os.path.dirname(src_css), url.split("?")[0].split("#")[0]))
        if not src.startswith(os.path.normpath(wp_root) + os.sep) or not os.path.isfile(src):
            continue
        dest = os.path.normpath(os.path.join(os.path.dirname(dest_css), url.split("?")[0].split("#")[0]))
        if dest.startswith(os.path.normpath(preview_dir) + os.sep) and not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)


class Harness:
    def __init__(self, runtime: str, wpcli: str, content: str, port: int):
        self.runtime = os.path.abspath(runtime)
        self.wp = os.path.join(self.runtime, "wordpress")
        self.wpcli = wpcli
        self.content_path = content
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.out = os.path.join(self.runtime, "rendered")
        self.report: Dict = {"base": self.base, "lint": [], "setup": {"ok": False, "steps": [], "errors": []},
                             "pages": []}
        home = os.path.join(self.runtime, "home")
        tmp = os.path.join(self.runtime, "tmp")
        for d in (home, tmp):
            os.makedirs(d, exist_ok=True)
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": home, "TMPDIR": tmp, "LANG": "C.UTF-8",
                    "WP_CLI_CACHE_DIR": os.path.join(home, "wp-cli-cache"), "WP_CLI_DISABLE_AUTO_CHECK_UPDATE": "1",
                    "WP_CLI_CONFIG_PATH": os.path.join(home, "wp-cli.yml")}

    def run(self, argv: List[str], timeout: int = 120) -> Tuple[int, str]:
        try:
            p = subprocess.run(argv, cwd=self.wp, env=self.env, capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s: {' '.join(argv[:4])}"

    def wp_cli(self, *args: str, timeout: int = 120) -> Tuple[int, str]:
        return self.run(["php", "-d", "memory_limit=512M", self.wpcli, *args, "--path=" + self.wp, "--allow-root"],
                        timeout)

    def step(self, name: str, rc: int, out: str, required: bool = True) -> bool:
        self.report["setup"]["steps"].append({"step": name, "ok": rc == 0, "output": out.strip()[-600:]})
        if rc != 0 and required:
            self.report["setup"]["errors"].append(f"{name}: {out.strip()[-400:]}")
        return rc == 0

    def lint(self) -> None:
        theme = os.path.join(self.wp, "wp-content", "themes", THEME_SLUG)
        for folder, _, files in os.walk(theme):
            for name in sorted(files):
                if name.endswith(".php"):
                    path = os.path.join(folder, name)
                    rc, out = self.run(["php", "-l", path], 30)
                    if rc != 0:
                        rel = os.path.relpath(path, theme)
                        self.report["lint"].append(f"theme/{rel}: " + out.strip().replace(path, rel)[-300:])

    def setup(self, content: Dict) -> bool:
        title = str(content.get("site_title") or "Website")[:120]
        ok = self.step("install WordPress", *self.wp_cli(
            "core", "install", "--url=" + self.base, "--title=" + title, "--admin_user=hood",
            "--admin_password=" + secrets.token_urlsafe(18), "--admin_email=hood@example.invalid", "--skip-email"))
        if not ok:
            return False
        ok = self.step("activate the agents' theme", *self.wp_cli("theme", "activate", THEME_SLUG))
        if not ok:
            return False
        self.step("site tagline", *self.wp_cli("option", "update", "blogdescription",
                                               str(content.get("tagline") or "")[:200]), required=False)
        self.step("pretty links", *self.wp_cli("rewrite", "structure", "/%postname%/"), required=False)
        ids: Dict[str, str] = {}
        for order, page in enumerate(content.get("pages") or []):
            slug = str(page.get("slug") or "").strip().strip("/")
            rc, out = self.wp_cli("post", "create", "--post_type=page", "--post_status=publish",
                                  "--post_title=" + str(page.get("title") or slug)[:200], "--post_name=" + slug,
                                  "--post_content=" + str(page.get("content") or ""), f"--menu_order={order}",
                                  "--porcelain")
            if self.step(f"create page '{slug}'", rc, out):
                ids[slug] = out.strip().splitlines()[-1].strip()
        front = str(content.get("front_page") or "").strip().strip("/")
        if front and front in ids:
            self.step("front page", *self.wp_cli("option", "update", "show_on_front", "page"))
            self.step("front page id", *self.wp_cli("option", "update", "page_on_front", ids[front]))
        menu = [s for s in (content.get("menu") or list(ids)) if s in ids]
        if menu:
            rc, out = self.wp_cli("menu", "create", "Main", "--porcelain")
            if self.step("create menu", rc, out, required=False):
                for slug in menu:
                    self.step(f"menu item '{slug}'", *self.wp_cli("menu", "item", "add-post", "Main", ids[slug]),
                              required=False)
                locations = self.wp_cli("menu", "location", "list", "--format=ids")[1].split()
                if locations:
                    self.step("menu location", *self.wp_cli("menu", "location", "assign", "Main", locations[0]),
                              required=False)
        self.report["setup"]["pages"] = ids
        self.report["setup"]["ok"] = not self.report["setup"]["errors"]
        return self.report["setup"]["ok"]

    def serve_and_crawl(self, content: Dict) -> None:
        router = os.path.join(self.wp, "hood-router.php")
        server = subprocess.Popen(["php", "-S", f"127.0.0.1:{self.port}", "-t", self.wp, router], cwd=self.wp,
                                  env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                    break
                except OSError:
                    time.sleep(0.2)
            queue = ["/"] + [f"/{str(p.get('slug')).strip('/')}/" for p in (content.get("pages") or [])
                             if p.get("slug")]
            seen: set = set()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while queue and len(seen) < MAX_PAGES:
                path = queue.pop(0)
                if path in seen:
                    continue
                seen.add(path)
                status, html = 0, ""
                try:
                    with opener.open(self.base + path, timeout=30) as resp:
                        status, html = resp.status, resp.read(5_000_000).decode("utf-8", errors="replace")
                except urllib.error.HTTPError as exc:
                    status, html = exc.code, exc.read(200_000).decode("utf-8", errors="replace")
                except (urllib.error.URLError, OSError) as exc:
                    status, html = 0, f"<!-- request failed: {exc} -->"
                key = page_key(path)
                dest = os.path.join(self.out, "pages", key + ".html")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(html)
                self.report["pages"].append({"path": path, "key": key, "status": status,
                                             "php_errors": php_errors(html), "bytes": len(html)})
                if status == 200:
                    queue.extend(p for p in internal_paths(html, self.base) if p not in seen)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()

    def preview(self) -> None:
        keys = {p["key"] for p in self.report["pages"] if p["status"] == 200}
        preview_dir = os.path.join(self.out, "preview")
        for p in self.report["pages"]:
            if p["status"] != 200:
                continue
            with open(os.path.join(self.out, "pages", p["key"] + ".html"), encoding="utf-8") as fh:
                html = fh.read()
            dest = os.path.join(preview_dir, p["key"] + ".html")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(rewrite_for_preview(html, self.base, p["key"], keys, self.wp, preview_dir))

    def main(self) -> int:
        try:
            with open(self.content_path, encoding="utf-8") as fh:
                content = json.load(fh)
        except (OSError, ValueError) as exc:
            self.report["setup"]["errors"].append(f"content/site.json could not be read: {exc}")
            content = None
        self.lint()
        if content is not None and not self.report["lint"] and self.setup(content):
            self.serve_and_crawl(content)
            self.preview()
        with open(os.path.join(self.out, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(self.report, fh, indent=1)
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--wpcli", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args(argv)
    os.makedirs(os.path.join(a.runtime, "rendered"), exist_ok=True)
    return Harness(a.runtime, a.wpcli, a.content, a.port).main()


if __name__ == "__main__":
    sys.exit(main())
