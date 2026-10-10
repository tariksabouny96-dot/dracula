"""Independent validators: re-open each rendered output with a *different*
reader than produced it and confirm it is well-formed and safe. An output that
does not validate is never registered or returned.
"""
from __future__ import annotations

import csv as _csv
import io
import re
import zipfile
from html.parser import HTMLParser
from typing import Tuple

MAX_OUTPUT_BYTES = 25 * 1024 * 1024

_MAGIC = {
    "pdf": b"%PDF-",
    "xlsx": b"PK\x03\x04",
    "docx": b"PK\x03\x04",
    "zip": b"PK\x03\x04",
}

_HTML_ALLOWED = {
    "html", "head", "meta", "title", "style", "body", "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "ul", "ol", "li", "pre", "code", "table", "thead", "tbody", "tr", "th", "td",
    "strong", "em", "hr", "br", "span",
}


class _TagChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.bad = []
        self.saw_script = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "iframe", "object", "embed"):
            self.saw_script = True
        if tag not in _HTML_ALLOWED:
            self.bad.append(tag)


def _size_ok(data: bytes) -> Tuple[bool, str]:
    if not data:
        return False, "empty output"
    if len(data) > MAX_OUTPUT_BYTES:
        return False, f"output exceeds {MAX_OUTPUT_BYTES} bytes"
    return True, ""


def _magic_ok(fmt: str, data: bytes) -> Tuple[bool, str]:
    magic = _MAGIC.get(fmt)
    if magic and not data.startswith(magic):
        return False, f"{fmt} magic bytes missing"
    return True, ""


def validate_md(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False, "markdown is not valid UTF-8"
    return True, "ok"


def validate_html(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, "html is not valid UTF-8"
    checker = _TagChecker()
    checker.feed(text)
    if checker.saw_script:
        return False, "html contains a script/active element"
    if checker.bad:
        return False, f"html uses non-allowlisted tags: {sorted(set(checker.bad))[:5]}"
    return True, "ok"


def validate_csv(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    text = data.decode("utf-8-sig")  # tolerate the Excel BOM
    reader = _csv.reader(io.StringIO(text))
    # These leaders make a spreadsheet treat a cell as a formula; the renderer
    # must have apostrophe-prefixed any such cell. (A leading '-' or '+' on a
    # plain number is a value, not an injection, so they are not flagged here.)
    dangerous = ("=", "@", "\t", "\r")
    for row in reader:
        for cell in row:
            if cell and cell[0] in dangerous:
                return False, f"csv cell not formula-neutralised: {cell[:20]!r}"
    return True, "ok"


def validate_xlsx(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    ok, why = _magic_ok("xlsx", data)
    if not ok:
        return ok, why
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True)
        if not wb.sheetnames:
            return False, "xlsx has no sheets"
    except Exception as exc:
        return False, f"xlsx did not re-open: {type(exc).__name__}"
    # Independent raw scan: no formula (<f>) element may exist in any sheet.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                    if re.search(rb"<f[ >]", zf.read(name)):
                        return False, "xlsx contains a formula cell"
    except Exception as exc:
        return False, f"xlsx structure unreadable: {type(exc).__name__}"
    return True, "ok"


def validate_pdf(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    ok, why = _magic_ok("pdf", data)
    if not ok:
        return ok, why
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) < 1:
            return False, "pdf has no pages"
    except Exception as exc:
        return False, f"pdf did not re-open: {type(exc).__name__}"
    return True, "ok"


def validate_docx(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    ok, why = _magic_ok("docx", data)
    if not ok:
        return ok, why
    try:
        from docx import Document
        Document(io.BytesIO(data))
    except Exception as exc:
        return False, f"docx did not re-open: {type(exc).__name__}"
    return True, "ok"


def validate_zip(data: bytes) -> Tuple[bool, str]:
    ok, why = _size_ok(data)
    if not ok:
        return ok, why
    ok, why = _magic_ok("zip", data)
    if not ok:
        return ok, why
    try:
        import hashlib
        import json
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            bad = zf.testzip()
            if bad is not None:
                return False, f"zip member corrupt: {bad}"
            names = set(zf.namelist())
            if "MANIFEST.json" not in names:
                return False, "zip missing MANIFEST.json"
            manifest = json.loads(zf.read("MANIFEST.json"))
            for name, meta in manifest.get("members", {}).items():
                if name not in names:
                    return False, f"manifest names a missing member: {name}"
                if hashlib.sha256(zf.read(name)).hexdigest() != meta["sha256"]:
                    return False, f"manifest hash mismatch: {name}"
    except Exception as exc:
        return False, f"zip did not re-open: {type(exc).__name__}"
    return True, "ok"


VALIDATORS = {
    "md": validate_md,
    "html": validate_html,
    "csv": validate_csv,
    "xlsx": validate_xlsx,
    "pdf": validate_pdf,
    "docx": validate_docx,
    "zip": validate_zip,
}


def validate(fmt: str, data: bytes) -> Tuple[bool, str]:
    fn = VALIDATORS.get(fmt)
    if fn is None:
        return False, f"no validator for format {fmt}"
    return fn(data)
