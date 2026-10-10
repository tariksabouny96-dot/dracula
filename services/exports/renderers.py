"""Renderers: DocumentSpec -> bytes, one function per format.

Every renderer escapes untrusted text for its target format. Spreadsheet
renderers (xlsx, csv) defend against formula injection: any cell whose text
begins with one of = + - @ TAB CR is neutralised so a spreadsheet application
can never execute it as a formula.
"""
from __future__ import annotations

import csv as _csv
import hashlib
import html as _html
import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from .spec import DocumentSpec

_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _formula_safe(value: str) -> str:
    """Prefix a leading formula trigger with an apostrophe (CSV convention)."""
    if value and value[0] in _FORMULA_LEADERS:
        return "'" + value
    return value


# --------------------------------------------------------------------- markdown
def render_md(spec: DocumentSpec) -> bytes:
    out: List[str] = [f"# {spec.title}"]
    meta = " · ".join(x for x in [spec.author, spec.date] if x)
    if meta:
        out.append(f"_{meta}_")
    for b in spec.blocks:
        t = b.type
        if t == "heading":
            out.append(f"{'#' * b.level} {b.text}")
        elif t == "paragraph":
            out.append(b.text)
        elif t == "bullet_list":
            out.extend(f"- {i}" for i in b.items)
        elif t == "numbered_list":
            out.extend(f"{n}. {i}" for n, i in enumerate(b.items, 1))
        elif t == "code":
            out.append(f"```{b.language}\n{b.text}\n```")
        elif t == "table":
            if b.title:
                out.append(f"**{b.title}**")
            esc = lambda s: s.replace("|", "\\|").replace("\n", " ")
            out.append("| " + " | ".join(esc(c) for c in b.columns) + " |")
            out.append("| " + " | ".join("---" for _ in b.columns) + " |")
            out.extend("| " + " | ".join(esc(c) for c in row) + " |" for row in b.rows)
        elif t == "page_break":
            out.append("\n---\n")
        out.append("")
    return ("\n".join(out).rstrip() + "\n").encode("utf-8")


# ------------------------------------------------------------------------- html
_HTML_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
<title>{title}</title>
<style>
:root{{color-scheme:light dark}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:48rem;margin:2rem auto;padding:0 1rem;line-height:1.6;background:#fff;color:#111}}
@media (prefers-color-scheme:dark){{body{{background:#14151a;color:#e6e6e6}}code,pre{{background:#23252b}}th{{background:#23252b}}}}
h1,h2,h3,h4,h5,h6{{line-height:1.25}}
code,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f3f4f6;border-radius:4px}}
pre{{padding:.75rem;overflow:auto}} code{{padding:.1rem .3rem}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border:1px solid #8884;padding:.4rem .6rem;text-align:left;vertical-align:top}}
th{{background:#f3f4f6}}
.meta{{color:#8a8a8a;font-size:.9rem}}
</style></head><body>
"""


def render_html(spec: DocumentSpec) -> bytes:
    e = _html.escape
    parts: List[str] = [_HTML_HEAD.format(title=e(spec.title)), f"<h1>{e(spec.title)}</h1>"]
    meta = " · ".join(e(x) for x in [spec.author, spec.date] if x)
    if meta:
        parts.append(f'<p class="meta">{meta}</p>')
    for b in spec.blocks:
        t = b.type
        if t == "heading":
            parts.append(f"<h{b.level}>{e(b.text)}</h{b.level}>")
        elif t == "paragraph":
            parts.append(f"<p>{e(b.text)}</p>")
        elif t == "bullet_list":
            parts.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in b.items) + "</ul>")
        elif t == "numbered_list":
            parts.append("<ol>" + "".join(f"<li>{e(i)}</li>" for i in b.items) + "</ol>")
        elif t == "code":
            parts.append(f"<pre><code>{e(b.text)}</code></pre>")
        elif t == "table":
            if b.title:
                parts.append(f"<p><strong>{e(b.title)}</strong></p>")
            head = "".join(f"<th>{e(c)}</th>" for c in b.columns)
            body = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row) + "</tr>" for row in b.rows)
            parts.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
        elif t == "page_break":
            parts.append('<hr style="page-break-after:always">')
    parts.append("</body></html>\n")
    return "\n".join(parts).encode("utf-8")


# -------------------------------------------------------------------------- csv
def _tables(spec: DocumentSpec):
    idx = 0
    for b in spec.blocks:
        if b.type == "table":
            idx += 1
            yield idx, b


def render_csv(spec: DocumentSpec) -> bytes:
    """One CSV covering every table (BOM for Excel). No tables -> header only."""
    buf = io.StringIO()
    w = _csv.writer(buf)
    any_table = False
    for idx, tbl in _tables(spec):
        any_table = True
        if tbl.title:
            w.writerow([f"# {tbl.title}"])
        w.writerow([_formula_safe(c) for c in tbl.columns])
        for row in tbl.rows:
            w.writerow([_formula_safe(c) for c in row])
        w.writerow([])
    if not any_table:
        w.writerow(["(no tabular data in document)"])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


# ------------------------------------------------------------------------- xlsx
def render_xlsx(spec: DocumentSpec) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="DDDDDD")

    def _add_sheet(title: str, columns, rows):
        ws = wb.create_sheet(title[:31] or "Sheet")
        ws.append(list(columns))
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            # Never let a header be interpreted as a formula.
            if isinstance(cell.value, str):
                cell.data_type = "s"
        for row in rows:
            ws.append(list(row))
            for cell in ws[ws.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"  # store as string; openpyxl won't emit a formula
        ws.freeze_panes = "A2"
        for i, col in enumerate(columns, 1):
            width = max([len(str(col))] + [len(str(r[i - 1])) for r in rows] + [8])
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(width + 2, 60)

    tables = list(_tables(spec))
    if not tables:
        _add_sheet("Document", ["field", "value"],
                   [["title", spec.title]] + ([["author", spec.author]] if spec.author else []))
    else:
        seen = {}
        for idx, tbl in tables:
            base = (tbl.title or f"Table {idx}")[:31]
            name = base
            seen[base] = seen.get(base, 0) + 1
            if seen[base] > 1:
                name = f"{base[:28]}_{seen[base]}"
            _add_sheet(name, tbl.columns, tbl.rows)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# -------------------------------------------------------------------------- pdf
def render_pdf(spec: DocumentSpec) -> Tuple[bytes, Dict]:
    """Render with a built-in core font (no bundled TTF needed). Characters the
    Latin-1 core font cannot draw are replaced with '?' and counted, so the
    result is honest about any loss rather than silently dropping text."""
    from fpdf import FPDF

    replaced = 0

    def enc(text: str) -> str:
        nonlocal replaced
        out = text.encode("latin-1", "replace").decode("latin-1")
        replaced += sum(1 for a, b in zip(text, out) if b == "?" and a != "?")
        return out

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_title(spec.title)
    if spec.author:
        pdf.set_author(spec.author)
    pdf.add_page()
    epw = pdf.epw  # effective page width

    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(epw, 9, enc(spec.title))
    meta = " / ".join(x for x in [spec.author, spec.date] if x)
    if meta:
        pdf.set_font("Helvetica", "I", 10)
        pdf.set_text_color(120, 120, 120)
        pdf.multi_cell(epw, 6, enc(meta))
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    for b in spec.blocks:
        t = b.type
        if t == "heading":
            size = max(10, 18 - b.level * 2)
            pdf.set_font("Helvetica", "B", size)
            pdf.multi_cell(epw, size * 0.5, enc(b.text))
            pdf.ln(1)
        elif t == "paragraph":
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(epw, 6, enc(b.text))
            pdf.ln(1)
        elif t in ("bullet_list", "numbered_list"):
            pdf.set_font("Helvetica", "", 11)
            for n, item in enumerate(b.items, 1):
                marker = "- " if t == "bullet_list" else f"{n}. "
                pdf.multi_cell(epw, 6, enc(marker + item))
            pdf.ln(1)
        elif t == "code":
            pdf.set_font("Courier", "", 9)
            pdf.set_fill_color(244, 244, 246)
            for line in b.text.split("\n"):
                pdf.multi_cell(epw, 5, enc(line) or " ", fill=True)
            pdf.ln(1)
        elif t == "table":
            if b.title:
                pdf.set_font("Helvetica", "B", 11)
                pdf.multi_cell(epw, 6, enc(b.title))
            ncol = len(b.columns)
            cw = epw / ncol
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_fill_color(221, 221, 221)
            for c in b.columns:
                pdf.cell(cw, 7, enc(c)[:40], border=1, fill=True)
            pdf.ln()
            pdf.set_font("Helvetica", "", 9)
            for row in b.rows:
                for c in row:
                    pdf.cell(cw, 6, enc(c)[:40], border=1)
                pdf.ln()
            pdf.ln(1)
        elif t == "page_break":
            pdf.add_page()

    # Page footer "Page N of M".
    total = pdf.pages_count
    for n in range(1, total + 1):
        pdf.page = n
        pdf.set_y(-12)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(130, 130, 130)
        pdf.cell(0, 8, f"Page {n} of {total}", align="C")
    pdf.set_text_color(0, 0, 0)

    data = pdf.output()
    return bytes(data), {"chars_replaced": replaced}


# ------------------------------------------------------------------------- docx
def render_docx(spec: DocumentSpec) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.core_properties.title = spec.title
    if spec.author:
        doc.core_properties.author = spec.author
    doc.add_heading(spec.title, level=0)
    meta = " / ".join(x for x in [spec.author, spec.date] if x)
    if meta:
        doc.add_paragraph(meta, style="Subtitle" if "Subtitle" in [s.name for s in doc.styles] else None)

    for b in spec.blocks:
        t = b.type
        if t == "heading":
            doc.add_heading(b.text, level=b.level)
        elif t == "paragraph":
            doc.add_paragraph(b.text)
        elif t == "bullet_list":
            for i in b.items:
                doc.add_paragraph(i, style="List Bullet")
        elif t == "numbered_list":
            for i in b.items:
                doc.add_paragraph(i, style="List Number")
        elif t == "code":
            p = doc.add_paragraph()
            run = p.add_run(b.text)
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif t == "table":
            if b.title:
                doc.add_paragraph(b.title).runs[0].bold = True
            table = doc.add_table(rows=1, cols=len(b.columns))
            table.style = "Table Grid"
            for cell, col in zip(table.rows[0].cells, b.columns):
                run = cell.paragraphs[0].add_run(col)
                run.bold = True
            for row in b.rows:
                cells = table.add_row().cells
                for cell, val in zip(cells, row):
                    cell.text = val
        elif t == "page_break":
            doc.add_page_break()

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# -------------------------------------------------------------------------- zip
def render_zip(members: Dict[str, bytes]) -> bytes:
    """Bundle already-rendered members plus a MANIFEST.json of SHA-256 hashes."""
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "members": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
                    for name, data in sorted(members.items())},
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(members.items()):
            zf.writestr(name, data)
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2).encode("utf-8"))
    return out.getvalue()


# extension + mime per format
FORMAT_MEDIA = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "html": ("text/html; charset=utf-8", "html"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pdf": ("application/pdf", "pdf"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "zip": ("application/zip", "zip"),
}
