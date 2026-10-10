"""Unit tests for the document-export module (F21): spec, renderers,
independent validators, artifact registry, and the end-to-end service."""
import time

import pytest

from services.exports.spec import DocumentSpec
from services.exports import renderers as R, validators as V
from services.exports.service import ExportService
from services.artifacts.registry import ArtifactRegistry


SPEC = {
    "title": "Quarterly Report", "author": "Zak", "date": "2026-10-09",
    "blocks": [
        {"type": "heading", "level": 1, "text": "Summary"},
        {"type": "paragraph", "text": "Revenue grew. <script>alert(1)</script> & more."},
        {"type": "bullet_list", "items": ["Point A", "Point B"]},
        {"type": "numbered_list", "items": ["First", "Second"]},
        {"type": "code", "language": "python", "text": "print('hi')"},
        {"type": "table", "title": "KPIs", "columns": ["Metric", "Value"],
         "rows": [["Users", "1200"], ["=HYPERLINK(1)", "-3"]]},
        {"type": "page_break"},
    ],
}


# ---- spec -----------------------------------------------------------------
def test_spec_rejects_bad_input():
    for bad in [
        {"title": "x", "blocks": [{"type": "heading", "level": 9, "text": "t"}]},
        {"title": "x", "blocks": [{"type": "paragraph", "text": "a\x00b"}]},
        {"title": "x", "blocks": [{"type": "bogus"}]},
        {"title": "x", "extra": 1, "blocks": []},
        {"title": "x", "blocks": [{"type": "table", "columns": ["a"], "rows": [["1", "2"]]}]},
    ]:
        with pytest.raises(Exception):
            DocumentSpec.parse(bad)


def test_spec_byte_ceiling():
    huge = {"title": "x", "blocks": [{"type": "paragraph", "text": "a" * 50000}] * 60}
    with pytest.raises(ValueError):
        DocumentSpec.parse(huge)


# ---- renderers + validators ----------------------------------------------
@pytest.mark.parametrize("fmt", ["md", "html", "csv", "xlsx", "pdf", "docx"])
def test_each_format_renders_and_validates(fmt):
    spec = DocumentSpec.parse(SPEC)
    data = R.render_pdf(spec)[0] if fmt == "pdf" else {
        "md": R.render_md, "html": R.render_html, "csv": R.render_csv,
        "xlsx": R.render_xlsx, "docx": R.render_docx}[fmt](spec)
    ok, why = V.validate(fmt, data)
    assert ok, f"{fmt} failed validation: {why}"


def test_html_escapes_untrusted_markup():
    html = R.render_html(DocumentSpec.parse(SPEC))
    assert b"<script>alert(1)</script>" not in html
    assert b"&lt;script&gt;" in html
    assert V.validate("html", html)[0]


def test_csv_and_xlsx_neutralise_formula_injection():
    spec = DocumentSpec.parse(SPEC)
    csv = R.render_csv(spec)
    assert b"'=HYPERLINK(1)" in csv
    assert V.validate("csv", csv) == (True, "ok")
    # The xlsx validator independently scans for any formula element.
    assert V.validate("xlsx", R.render_xlsx(spec)) == (True, "ok")


def test_validators_reject_corrupt_or_active_content():
    assert V.validate("pdf", b"not a pdf")[0] is False
    assert V.validate("html", b"<html><body><script>x</script></body></html>")[0] is False
    assert V.validate("zip", b"PK\x03\x04 garbage")[0] is False


def test_zip_manifest_hashes_members():
    spec = DocumentSpec.parse(SPEC)
    members = {"doc.pdf": R.render_pdf(spec)[0], "doc.html": R.render_html(spec)}
    z = R.render_zip(members)
    ok, why = V.validate("zip", z)
    assert ok, why


# ---- registry -------------------------------------------------------------
def test_registry_owner_isolation_and_single_use_tokens(tmp_path):
    reg = ArtifactRegistry(tmp_path / "artifacts", signing_key=b"k")
    art = reg.store("alice", "r.pdf", "application/pdf", b"%PDF-1.4 x")
    aid = art["artifact_id"]
    assert len(reg.list("alice")) == 1 and reg.list("bob") == []
    with pytest.raises(KeyError):
        reg.get("bob", aid)
    with pytest.raises(KeyError):
        reg.mint_token("bob", aid)
    tok = reg.mint_token("alice", aid, ttl_seconds=60)["token"]
    name, mime, data = reg.redeem(tok, expected_owner="alice")
    assert name == "r.pdf" and data.startswith(b"%PDF")
    with pytest.raises(KeyError):
        reg.redeem(tok, expected_owner="alice")  # single use


def test_registry_token_binding_and_expiry(tmp_path):
    reg = ArtifactRegistry(tmp_path / "artifacts", signing_key=b"k")
    aid = reg.store("alice", "r.pdf", "application/pdf", b"%PDF-1.4 x")["artifact_id"]
    tok = reg.mint_token("alice", aid, ttl_seconds=60)["token"]
    with pytest.raises(KeyError):
        reg.redeem(tok, expected_owner="mallory")      # another owner's token
    with pytest.raises(KeyError):
        reg.redeem(tok[:-3] + "zzz", expected_owner="alice")  # tampered signature
    short = reg.mint_token("alice", aid, ttl_seconds=1)["token"]
    time.sleep(1.2)
    with pytest.raises(KeyError):
        reg.redeem(short, expected_owner="alice")      # expired


def test_registry_rehashes_on_download(tmp_path):
    reg = ArtifactRegistry(tmp_path / "artifacts", signing_key=b"k")
    art = reg.store("alice", "r.txt", "text/plain", b"original")
    # Tamper with the stored blob on disk.
    (tmp_path / "artifacts" / "blobs" / art["artifact_id"]).write_bytes(b"tampered")
    tok = reg.mint_token("alice", art["artifact_id"])["token"]
    with pytest.raises(Exception):
        reg.redeem(tok, expected_owner="alice")


# ---- service --------------------------------------------------------------
def test_service_exports_all_formats_and_isolates_owners(tmp_path):
    svc = ExportService(tmp_path / "exports")
    rec = svc.export("alice", SPEC, ["md", "html", "csv", "xlsx", "pdf", "docx", "zip"])
    assert rec["status"] == "complete"
    assert all(o["status"] == "ok" for o in rec["outputs"])
    assert len(svc.list("alice")) == 1 and svc.list("bob") == []
    with pytest.raises(KeyError):
        svc.get("bob", rec["export_id"])
    got = svc.get("alice", rec["export_id"])
    assert got["export_id"] == rec["export_id"]


def test_service_rejects_bad_formats_and_self_tests(tmp_path):
    svc = ExportService(tmp_path / "exports")
    with pytest.raises(ValueError):
        svc.export("alice", SPEC, ["exe"])
    assert all(v == "ok" for v in svc.self_test().values())


def test_service_respects_emergency_stop(tmp_path):
    from packages.security import StopLatch
    latch = StopLatch()
    svc = ExportService(tmp_path / "exports", stop_latch=latch)
    latch.engage("test stop")
    with pytest.raises(Exception):
        svc.export("alice", SPEC, ["pdf"])
