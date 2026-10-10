"""Static website missions: deterministic checks that never execute generated code.

A website made of HTML, CSS and JavaScript can be judged by reading its files:
pages parse, required head elements exist, every local link and asset resolves,
stylesheets and scripts are structurally sound, and the QA agent's independent
acceptance checks (a declarative JSON spec, not code) hold on the parsed pages.
Nothing the agents wrote is run, so this works on any host, including Windows,
where Hood has no process sandbox.

What this does NOT prove: that the JavaScript behaves correctly at run time.
The JavaScript check is a structural lexer (brackets, strings, comments, template
literals, regular expressions), not an interpreter. Reports say so.
"""
from __future__ import annotations

import json
import posixpath
import re
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from .contracts import CheckResult, VerificationDecision, VerificationVerdict

SITE_ROOT = "site"
SPEC_PATH = "qa_checks/acceptance.json"
MAX_SPEC_CHECKS = 60
CHECK_TYPES = {"page_exists", "contains_text", "has_element", "links_to"}

VOID_ELEMENTS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
                 "track", "wbr", "param", "keygen"}
# Elements whose end tag HTML lets authors omit; an unclosed one is not an error.
OPTIONAL_END = {"html", "head", "body", "p", "li", "dt", "dd", "option", "optgroup", "tr", "td", "th",
                "thead", "tbody", "tfoot", "colgroup", "rt", "rp", "caption"}
RAW_TEXT = {"script", "style"}
_LOCAL_REF_ATTRS = {("a", "href"), ("link", "href"), ("script", "src"), ("img", "src"), ("source", "src"),
                    ("video", "src"), ("audio", "src"), ("iframe", "src"), ("video", "poster"),
                    ("input", "src"), ("embed", "src")}
_MUST_BE_LOCAL = {"script", "link", "iframe"}  # scripts, stylesheets and frames must ship with the site


# ------------------------------------------------------------------ HTML model
class Element:
    __slots__ = ("tag", "attrs", "line", "text_parts")

    def __init__(self, tag: str, attrs: Dict[str, str], line: int):
        self.tag, self.attrs, self.line = tag, attrs, line
        self.text_parts: List[str] = []

    @property
    def text(self) -> str:
        return _norm("".join(self.text_parts))

    @property
    def classes(self) -> List[str]:
        return (self.attrs.get("class") or "").split()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class Page(HTMLParser):
    """Parse one HTML file into elements, visible text, inline code and structural errors."""

    def __init__(self, path: str, source: str):
        super().__init__(convert_charrefs=True)
        self.path = path
        self.doctype = ""
        self.elements: List[Element] = []
        self.errors: List[str] = []
        self.inline_scripts: List[Tuple[int, str, str]] = []   # (line, type, code)
        self.inline_styles: List[Tuple[int, str]] = []
        self._stack: List[Element] = []
        self._visible: List[str] = []
        self._raw: Optional[Tuple[str, int, Dict[str, str]]] = None
        self._raw_buf: List[str] = []
        self.feed(source)
        self.close()
        for el in self._stack:
            if el.tag not in OPTIONAL_END:
                self.errors.append(f"line {el.line}: <{el.tag}> is never closed")

    # parser callbacks
    def handle_decl(self, decl):
        self.doctype = decl

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, self_closing=True)

    def _open(self, tag, attrs, self_closing):
        line = self.getpos()[0]
        values = {k.lower(): (v if v is not None else "") for k, v in attrs}
        el = Element(tag.lower(), values, line)
        self.elements.append(el)
        if el.tag in VOID_ELEMENTS or self_closing:
            return
        if el.tag in RAW_TEXT:
            self._raw, self._raw_buf = (el.tag, line, values), []
        # A new block closes an open <p>, as browsers do.
        if el.tag in {"div", "section", "ul", "ol", "table", "form", "h1", "h2", "h3", "h4", "h5", "h6",
                      "header", "footer", "nav", "main", "article", "aside", "p"}:
            if self._stack and self._stack[-1].tag == "p":
                self._stack.pop()
        if el.tag == "li" and self._stack and self._stack[-1].tag == "li":
            self._stack.pop()
        self._stack.append(el)

    def handle_endtag(self, tag):
        tag = tag.lower()
        line = self.getpos()[0]
        if tag in RAW_TEXT and self._raw and self._raw[0] == tag:
            _, start, attrs = self._raw
            code = "".join(self._raw_buf)
            if tag == "script" and not attrs.get("src"):
                self.inline_scripts.append((start, (attrs.get("type") or "").lower(), code))
            elif tag == "style":
                self.inline_styles.append((start, code))
            self._raw = None
        if tag in VOID_ELEMENTS:
            return
        if not any(el.tag == tag for el in self._stack):
            self.errors.append(f"line {line}: </{tag}> has no matching opening tag")
            return
        while self._stack:
            el = self._stack.pop()
            if el.tag == tag:
                break
            if el.tag not in OPTIONAL_END:
                self.errors.append(f"line {el.line}: <{el.tag}> is not closed before </{tag}> (line {line})")

    def handle_data(self, data):
        if self._raw:
            self._raw_buf.append(data)
            return
        self._visible.append(data)
        for el in self._stack:
            if sum(len(p) for p in el.text_parts) < 20_000:
                el.text_parts.append(data)

    # queries
    @property
    def visible_text(self) -> str:
        return _norm(" ".join(self._visible))

    def first(self, tag: str) -> Optional[Element]:
        return next((el for el in self.elements if el.tag == tag), None)

    def ids(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for el in self.elements:
            if el.attrs.get("id"):
                out[el.attrs["id"]] = out.get(el.attrs["id"], 0) + 1
        return out

    def select(self, selector: str) -> List[Element]:
        match = _parse_selector(selector)
        return [el for el in self.elements if _matches(el, match)]


# Simple selectors only: tag, #id, .class, [attr] and [attr=value], combined (no descendants).
_SELECTOR = re.compile(r"^(?P<tag>[a-zA-Z][a-zA-Z0-9-]*)?(?P<id>#[\w-]+)?(?P<classes>(?:\.[\w-]+)*)"
                       r"(?P<attrs>(?:\[[\w-]+(?:=(?:\"[^\"]*\"|'[^']*'|[^\]]*))?\])*)$")
_ATTR = re.compile(r"\[([\w-]+)(?:=(\"[^\"]*\"|'[^']*'|[^\]]*))?\]")


class SpecError(ValueError):
    """The QA agent's acceptance spec is malformed: the QA agent must fix it."""


def _parse_selector(selector: str) -> dict:
    sel = (selector or "").strip()
    m = _SELECTOR.fullmatch(sel)
    if not sel or not m:
        raise SpecError(f"Unsupported selector {selector!r} (use tag, #id, .class, [attr], [attr=value])")
    attrs = [(name.lower(), value.strip("\"'") if value is not None else None)
             for name, value in _ATTR.findall(m.group("attrs") or "")]
    return {"tag": (m.group("tag") or "").lower(), "id": (m.group("id") or "")[1:],
            "classes": [c for c in (m.group("classes") or "").split(".") if c], "attrs": attrs}


def _matches(el: Element, sel: dict) -> bool:
    if sel["tag"] and el.tag != sel["tag"]:
        return False
    if sel["id"] and el.attrs.get("id") != sel["id"]:
        return False
    if any(c not in el.classes for c in sel["classes"]):
        return False
    for name, value in sel["attrs"]:
        if name not in el.attrs or (value is not None and el.attrs[name] != value):
            return False
    return True


# ------------------------------------------------------------------ JavaScript / CSS lexers
_REGEX_AFTER_WORDS = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case",
                      "do", "else", "yield", "await"}
_PAIRS = {")": "(", "]": "[", "}": "{"}


def js_problems(src: str) -> List[str]:
    """Structural check of JavaScript source (never executed).

    Finds unbalanced or mismatched brackets, unterminated strings, template
    literals, comments and regular expressions. It is a lexer, not a parser.
    """
    problems: List[str] = []
    stack: List[Tuple[str, int]] = []   # "(" "[" "{" or "${" (template substitution)
    i, n, line = 0, len(src), 1
    prev = ""        # last significant token: "word", "num", "close" or an operator char
    prev_word = ""
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c in " \t\r\f\v﻿":
            i += 1
            continue
        if src.startswith("//", i) or src.startswith("<!--", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                problems.append(f"line {line}: comment /* is never closed")
                return problems
            line += src.count("\n", i, j)
            i = j + 2
            continue
        if c in "\"'":
            j, start = i + 1, line
            while j < n and src[j] != c:
                if src[j] == "\\":
                    if j + 1 < n and src[j + 1] == "\n":
                        line += 1
                    j += 2
                    continue
                if src[j] == "\n":
                    break
                j += 1
            if j >= n or src[j] != c:
                problems.append(f"line {start}: string is never closed")
                return problems
            i, prev = j + 1, "num"
            continue
        if c == "`":
            i, line, ok = _template(src, i + 1, line, stack)
            if not ok:
                problems.append(f"line {line}: template literal ` is never closed")
                return problems
            prev = "num" if src[i - 1] == "`" else "{"   # closed literal, or inside ${ ... }
            continue
        if c == "/":
            regex_ok = prev in ("", "(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*",
                                "%", "<", ">", "~", "^") or (prev == "word" and prev_word in _REGEX_AFTER_WORDS)
            if prev == "}":
                regex_ok = False
            if regex_ok:
                j, in_class = i + 1, False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        in_class = True
                    elif src[j] == "]":
                        in_class = False
                    elif src[j] == "/" and not in_class:
                        break
                    j += 1
                if j >= n or src[j] != "/":
                    problems.append(f"line {line}: regular expression is never closed")
                    return problems
                j += 1
                while j < n and (src[j].isalnum() or src[j] == "_"):
                    j += 1
                i, prev = j, "num"
                continue
            i, prev = i + 1, "/"
            continue
        if c in "([{":
            stack.append((c, line))
            i, prev = i + 1, c
            continue
        if c in ")]}":
            if stack and stack[-1][0] == "${" and c == "}":
                stack.pop()
                i, line, ok = _template(src, i + 1, line, stack)
                if not ok:
                    problems.append(f"line {line}: template literal ` is never closed")
                    return problems
                prev = "num" if src[i - 1] == "`" else "{"
                continue
            if not stack:
                problems.append(f"line {line}: '{c}' has no matching opening bracket")
                return problems
            opener, at = stack.pop()
            if opener != _PAIRS[c]:
                problems.append(f"line {line}: '{c}' closes '{opener}' opened on line {at}")
                return problems
            i, prev = i + 1, ("close" if c in ")]" else "}")
            continue
        if c.isalpha() or c in "_$" or ord(c) > 127:
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$" or ord(src[j]) > 127):
                j += 1
            prev, prev_word, i = "word", src[i:j], j
            continue
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] in "._"):
                j += 1
            i, prev = j, "num"
            continue
        i, prev = i + 1, c
    for opener, at in stack:
        problems.append(f"line {at}: '{'`' if opener == '${' else opener}' is never closed")
    return problems


def _template(src: str, i: int, line: int, stack: list) -> Tuple[int, int, bool]:
    """Scan template-literal text from i; returns (next index, line, closed?)."""
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            line += 1
        if c == "`":
            return i + 1, line, True
        if c == "$" and i + 1 < n and src[i + 1] == "{":
            stack.append(("${", line))
            return i + 2, line, True
        i += 1
    return i, line, False


def css_problems(src: str) -> List[str]:
    problems, depth, i, n, line = [], [], 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j < 0:
                return problems + [f"line {line}: comment /* is never closed"]
            line += src.count("\n", i, j)
            i = j + 2
            continue
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            if j >= n or src[j] != c:
                return problems + [f"line {line}: string is never closed"]
            i = j + 1
            continue
        elif c in "{(":
            depth.append((c, line))
        elif c in "})":
            want = "{" if c == "}" else "("
            if not depth or depth[-1][0] != want:
                return problems + [f"line {line}: '{c}' has no matching '{want}'"]
            depth.pop()
        i += 1
    return problems + [f"line {at}: '{c}' is never closed" for c, at in depth]


_CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.I)


# ------------------------------------------------------------------ reference resolution
def _resolve(page_rel: str, ref: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Classify a reference from a file under site/.

    Returns (kind, target path or None, fragment or None); kind is one of
    'external', 'special', 'fragment', 'local', 'outside'.
    """
    ref = (ref or "").strip()
    parts = urlsplit(ref)
    if parts.scheme in ("http", "https") or ref.startswith("//"):
        return "external", None, None
    if parts.scheme in ("mailto", "tel", "data", "javascript", "sms", "blob"):
        return "special", None, parts.scheme
    if parts.scheme:
        return "special", None, parts.scheme
    if not parts.path:
        return "fragment", page_rel, unquote(parts.fragment) if parts.fragment else None
    base = posixpath.dirname(page_rel)
    target = posixpath.normpath(posixpath.join(base, unquote(parts.path)))
    if parts.path.startswith("/") or not (target == SITE_ROOT or target.startswith(SITE_ROOT + "/")):
        return "outside", target, None
    if parts.path.endswith("/") or target == SITE_ROOT:
        target = posixpath.join(target, "index.html")
    return "local", target, unquote(parts.fragment) if parts.fragment else None


# ------------------------------------------------------------------ acceptance spec
def load_spec(text: str) -> List[dict]:
    """Validate the QA agent's declarative acceptance spec; raises SpecError."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SpecError(f"{SPEC_PATH} is not valid JSON: {exc.msg} (line {exc.lineno})") from None
    checks = data.get("checks") if isinstance(data, dict) else None
    if not isinstance(checks, list) or not checks:
        raise SpecError(f'{SPEC_PATH} must be an object with a non-empty "checks" list')
    if len(checks) > MAX_SPEC_CHECKS:
        raise SpecError(f"{SPEC_PATH} has more than {MAX_SPEC_CHECKS} checks")
    seen = set()
    for n, chk in enumerate(checks, 1):
        if not isinstance(chk, dict):
            raise SpecError(f"check {n} is not an object")
        kind, page = chk.get("type"), chk.get("page")
        if kind not in CHECK_TYPES:
            raise SpecError(f"check {n}: type must be one of {sorted(CHECK_TYPES)}")
        if not isinstance(page, str) or not page.endswith(".html") or ".." in page or page.startswith("/"):
            raise SpecError(f"check {n}: 'page' must be a page path relative to site/, e.g. index.html")
        cid = chk.get("id") or f"check_{n}"
        if not isinstance(cid, str) or cid in seen:
            raise SpecError(f"check {n}: duplicate or invalid id")
        seen.add(cid)
        if kind == "contains_text" and not (isinstance(chk.get("text"), str) and chk["text"].strip()):
            raise SpecError(f"check {cid}: contains_text needs a non-empty 'text'")
        if kind == "has_element":
            _parse_selector(chk.get("selector", ""))
            if "min_count" in chk and not (isinstance(chk["min_count"], int) and chk["min_count"] >= 1):
                raise SpecError(f"check {cid}: min_count must be a positive integer")
            if "text" in chk and not isinstance(chk["text"], str):
                raise SpecError(f"check {cid}: text must be a string")
        if kind == "links_to" and not (isinstance(chk.get("target"), str) and chk["target"].strip()):
            raise SpecError(f"check {cid}: links_to needs a 'target' page (e.g. menu.html)")
    return checks


def _run_spec(checks: List[dict], pages: Dict[str, Page]) -> Tuple[List[str], int]:
    lines, failed = [], 0
    for n, chk in enumerate(checks, 1):
        cid = chk.get("id") or f"check_{n}"
        page_rel = posixpath.normpath(posixpath.join(SITE_ROOT, chk["page"]))
        page = pages.get(page_rel)
        ok, why = False, ""
        if page is None:
            why = f"page {chk['page']} does not exist"
        elif chk["type"] == "page_exists":
            ok = True
        elif chk["type"] == "contains_text":
            ok = _norm(chk["text"]).lower() in page.visible_text.lower()
            why = "" if ok else f"text {chk['text']!r} not found on {chk['page']}"
        elif chk["type"] == "has_element":
            found = page.select(chk["selector"])
            if chk.get("text"):
                found = [el for el in found if _norm(chk["text"]).lower() in el.text.lower()]
            need = chk.get("min_count", 1)
            ok = len(found) >= need
            why = "" if ok else (f"{len(found)} element(s) match {chk['selector']!r}"
                                 + (f" containing {chk['text']!r}" if chk.get("text") else "")
                                 + f" on {chk['page']}, need {need}")
        elif chk["type"] == "links_to":
            target = chk["target"].strip()
            want_page, _, want_frag = target.partition("#")
            want = posixpath.normpath(posixpath.join(SITE_ROOT, want_page)) if want_page else page_rel
            ok = False
            for el in page.elements:
                if el.tag != "a" or "href" not in el.attrs:
                    continue
                kind, resolved, frag = _resolve(page_rel, el.attrs["href"])
                if kind in ("local", "fragment") and resolved == want and (not want_frag or frag == want_frag):
                    ok = True
                    break
            why = "" if ok else f"no link from {chk['page']} to {target}"
        failed += not ok
        desc = str(chk.get("description") or "")[:160]
        lines.append(f"{'PASS' if ok else 'FAIL'} {cid}" + (f" — {desc}" if desc else "") + (f" ({why})" if why else ""))
    return lines, failed


# ------------------------------------------------------------------ verifier
def _check(name: str, problems: List[str], notes: Optional[List[str]] = None, *, collected=None,
           suite_invalid=False) -> CheckResult:
    body = problems[:80] + ([f"... {len(problems) - 80} more"] if len(problems) > 80 else [])
    tail = "\n".join(body + ([""] if body and notes else []) + (notes or [])) or "no problems found"
    return CheckResult(name=name, command=["static-check", name], exit_code=0 if not problems else 1,
                       passed=not problems, output_tail=tail[-4000:], tests_collected=collected,
                       suite_invalid=suite_invalid)


def verify_static_site(workspace) -> VerificationDecision:
    """Judge a static website by reading its files only. Generated code is never executed."""
    digest = workspace.digest()
    listing = workspace.listing(limit=10_000)
    site_files = [p for p in listing if p.startswith(SITE_ROOT + "/")]
    if f"{SITE_ROOT}/index.html" not in site_files:
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=[], workspace_sha256=digest,
                                    reason="No home page: site/index.html was not written")
    texts = {p: (workspace.root / p).read_text(encoding="utf-8", errors="replace")
             for p in site_files if p.endswith((".html", ".css", ".js", ".json"))}
    existing = set(site_files)
    pages = {p: Page(p, texts[p]) for p in site_files if p.endswith(".html")}

    # 1. page structure
    structure, advisories = [], []
    for p, page in sorted(pages.items()):
        name = p[len(SITE_ROOT) + 1:]
        structure += [f"{name}: {e}" for e in page.errors]
        if not page.doctype.lower().startswith("doctype html"):
            structure.append(f"{name}: missing <!DOCTYPE html>")
        title = page.first("title")
        if title is None or not title.text:
            structure.append(f"{name}: missing or empty <title>")
        metas = [el for el in page.elements if el.tag == "meta"]
        if not any("charset" in m.attrs or m.attrs.get("http-equiv", "").lower() == "content-type" for m in metas):
            structure.append(f'{name}: missing <meta charset="utf-8">')
        if not any(m.attrs.get("name", "").lower() == "viewport" for m in metas):
            structure.append(f'{name}: missing <meta name="viewport"> (needed on phones)')
        html_el = page.first("html")
        if html_el is None or not html_el.attrs.get("lang"):
            advisories.append(f"{name}: <html> has no lang attribute")
        dupes = [i for i, count in page.ids().items() if count > 1]
        if dupes:
            structure.append(f"{name}: duplicate id(s) {', '.join(sorted(dupes)[:5])}")
        for el in page.elements:
            if el.tag == "img" and "alt" not in el.attrs:
                advisories.append(f"{name} line {el.line}: <img> has no alt text")

    # 2. links and assets
    links, linked_pages = [], {f"{SITE_ROOT}/index.html"}
    for p, page in sorted(pages.items()):
        name = p[len(SITE_ROOT) + 1:]
        for el in page.elements:
            for attr in ("href", "src", "poster"):
                if (el.tag, attr) not in _LOCAL_REF_ATTRS or attr not in el.attrs:
                    continue
                ref = el.attrs[attr]
                kind, target, frag = _resolve(p, ref)
                if kind == "external":
                    if el.tag in _MUST_BE_LOCAL:
                        links.append(f"{name} line {el.line}: <{el.tag}> loads {ref} from the internet "
                                     "(scripts, stylesheets and frames must be files in the site)")
                    elif el.tag != "a":
                        advisories.append(f"{name} line {el.line}: <{el.tag}> loads {ref} from the internet")
                    continue
                if kind == "special":
                    if frag == "javascript":
                        advisories.append(f"{name} line {el.line}: javascript: link (use a button instead)")
                    continue
                if kind == "outside":
                    links.append(f"{name} line {el.line}: {ref} points outside the site folder")
                    continue
                if kind == "fragment":
                    if frag and frag != "top" and frag not in page.ids():
                        links.append(f"{name} line {el.line}: link #{frag} has no element with that id")
                    continue
                if target not in existing:
                    links.append(f"{name} line {el.line}: {ref} does not exist")
                    continue
                if target.endswith(".html"):
                    linked_pages.add(target)
                    if frag and target in pages and frag not in pages[target].ids():
                        links.append(f"{name} line {el.line}: {ref} — no element with id '{frag}'")
    for p in sorted(set(pages) - linked_pages):
        advisories.append(f"{p[len(SITE_ROOT) + 1:]}: no page links to it")

    # 3. stylesheets (files, <style> blocks) and their url(...) assets
    css = []
    sheets = [(p, texts[p], 1) for p in site_files if p.endswith(".css")]
    sheets += [(p, code, start) for p, page in pages.items() for start, code in page.inline_styles]
    for p, code, start in sheets:
        name = p[len(SITE_ROOT) + 1:]
        css += [f"{name} (from line {start}): {e}" if start > 1 else f"{name}: {e}" for e in css_problems(code)]
        for _, ref in _CSS_URL.findall(code):
            kind, target, _ = _resolve(p, ref)
            if kind == "local" and target not in existing:
                css.append(f"{name}: url({ref}) does not exist")
            elif kind == "outside":
                css.append(f"{name}: url({ref}) points outside the site folder")
            elif kind == "external":
                advisories.append(f"{name}: url({ref}) loads from the internet")

    # 4. scripts (files, inline <script>) and JSON data — structure only, never executed
    js = []
    for p in site_files:
        if p.endswith(".js"):
            js += [f"{p[len(SITE_ROOT) + 1:]}: {e}" for e in js_problems(texts[p])]
        elif p.endswith(".json"):
            try:
                json.loads(texts[p])
            except json.JSONDecodeError as exc:
                js.append(f"{p[len(SITE_ROOT) + 1:]}: invalid JSON ({exc.msg}, line {exc.lineno})")
    for p, page in pages.items():
        for start, kind, code in page.inline_scripts:
            label = f"{p[len(SITE_ROOT) + 1:]} <script> at line {start}"
            if "json" in kind:
                try:
                    json.loads(code)
                except json.JSONDecodeError as exc:
                    js.append(f"{label}: invalid JSON ({exc.msg})")
            elif kind in ("", "module", "text/javascript", "application/javascript"):
                js += [f"{label}: {e}" for e in js_problems(code)]

    checks = [
        _check("site_structure", structure, [f"note: {a}" for a in advisories[:30]]),
        _check("links_and_assets", links),
        _check("css_syntax", css),
        _check("javascript_structure", js, ["(static lexer check: brackets, strings, comments; the code was "
                                            "not run)"] if any(p.endswith(".js") for p in site_files)
               or any(pg.inline_scripts for pg in pages.values()) else None),
    ]

    # 5. the QA agent's independent acceptance checks
    spec_text = texts.get(SPEC_PATH)
    if spec_text is None and (workspace.root / SPEC_PATH).is_file():
        spec_text = (workspace.root / SPEC_PATH).read_text(encoding="utf-8", errors="replace")
    collected = 0
    if spec_text is None:
        checks.append(_check("independent_acceptance_checks", [f"{SPEC_PATH} was not written"], collected=0))
    else:
        try:
            spec = load_spec(spec_text)
        except SpecError as exc:
            checks.append(_check("independent_acceptance_checks", [str(exc)], collected=0, suite_invalid=True))
        else:
            lines, failed = _run_spec(spec, pages)
            collected = len(spec)
            result = _check("independent_acceptance_checks",
                            [ln for ln in lines if ln.startswith("FAIL")],
                            [ln for ln in lines if ln.startswith("PASS")], collected=collected)
            result.output_tail = (f"{collected - failed} passed, {failed} failed\n" + result.output_tail)[-4000:]
            checks.append(result)

    if workspace.digest() != digest:
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=checks,
                                    workspace_sha256=workspace.digest(), reason="Workspace changed during verification")
    acceptance = checks[-1]
    if acceptance.suite_invalid or not collected:
        if acceptance.suite_invalid:
            return VerificationDecision(verdict=VerificationVerdict.FAIL, checks=checks, workspace_sha256=digest,
                                        reason="The QA acceptance checks are malformed")
        return VerificationDecision(verdict=VerificationVerdict.UNVERIFIED, checks=checks, workspace_sha256=digest,
                                    reason="No independent acceptance checks were written")
    failed = [c.name for c in checks if not c.passed]
    if failed:
        return VerificationDecision(verdict=VerificationVerdict.FAIL, checks=checks, workspace_sha256=digest,
                                    reason="Failed checks: " + ", ".join(failed))
    return VerificationDecision(verdict=VerificationVerdict.PASS, checks=checks, workspace_sha256=digest,
                                reason="All static checks passed (pages, links, CSS, JavaScript structure, "
                                       f"{collected} acceptance checks); the site's code was not run")
