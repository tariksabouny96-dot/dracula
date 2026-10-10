"""Toolbox: HOOD asks once per tool, installs only inside WSL2/Linux, verifies every download
against the publisher's checksum, and its root helper accepts only catalogued packages.
Downloads and system commands are faked; nothing downloaded is executed."""
import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path

import pytest

from services.firewall.policy import NetworkFirewall
from services.toolbox import TOOLS, Toolbox, ToolNotApproved, ToolUnavailable
from services.toolbox import service as svc
from services.toolbox.catalog import apt_packages, with_dependencies

REPO = Path(__file__).resolve().parents[2]


def _wp_tarball(version="7.1.3", extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {"wordpress/wp-includes/version.php": f"<?php\n$wp_version = '{version}';\n",
                 "wordpress/index.php": "<?php\n"}
        files.update(extra or {})
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _plugin(files, sums=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    sums = sums if sums is not None else {
        name.split("/", 1)[1]: {"sha256": hashlib.sha256(text.encode()).hexdigest()}
        for name, text in files.items() if not name.endswith("/")}
    return buf.getvalue(), json.dumps({"files": sums}).encode()


class FakeNet:
    def __init__(self, tarball=None, sha1=None, plugin=None):
        self.tarball = tarball or _wp_tarball()
        self.sha1 = sha1 or hashlib.sha1(self.tarball).hexdigest()
        files = {"sqlite-database-integration/load.php": "<?php\n/*\n * Version: 3.1.0\n */\n",
                 "sqlite-database-integration/db.copy": "<?php // {SQLITE_PLUGIN}\n"}
        self.plugin_zip, self.plugin_sums = plugin or _plugin(files)
        self.phar = b"phar bytes"
        self.calls = []

    def __call__(self, url, limit):
        self.calls.append(url)
        if url.endswith("latest.tar.gz.sha1"):
            return self.sha1.encode()
        if url.endswith("latest.tar.gz"):
            return self.tarball
        if url.endswith("wp-cli.phar.sha512"):
            return hashlib.sha512(self.phar).hexdigest().encode()
        if url.endswith("wp-cli.phar"):
            return self.phar
        if url.startswith("https://api.wordpress.org/plugins/info"):
            return json.dumps({"version": "3.1.0", "download_link":
                               "https://downloads.wordpress.org/plugin/sqlite-database-integration.3.1.0.zip"}).encode()
        if "plugin-checksums" in url:
            return self.plugin_sums
        if url.endswith(".zip"):
            return self.plugin_zip
        raise AssertionError("unexpected download " + url)


def php_present(argv, timeout):
    if argv[:2] == ["php", "-r"]:
        return 0, "8.3.6"
    if argv[:2] == ["php", "-m"]:
        return 0, "[PHP Modules]\nPDO\npdo_sqlite\nSQLite3\nmbstring\nxml\ndom\n"
    return 0, ""


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(Toolbox, "platform_problem", lambda self: None)
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/" + name if name == "php" else None)


def test_helper_allowlist_is_exactly_the_catalog():
    helper = (REPO / "scripts" / "wsl" / "hood-pkg").read_text()
    allowed = re.search(r'^ALLOWED="([^"]+)"', helper, re.M).group(1).split()
    assert sorted(allowed) == apt_packages()
    assert with_dependencies(["wp_sqlite"]) == ["php", "wordpress", "wp_sqlite"]


def test_owner_allows_once_then_hood_reuses_without_asking(tmp_path, linux):
    fw = NetworkFirewall(tmp_path / "fw")
    net = FakeNet()
    tb = Toolbox(tmp_path, firewall=fw, runner=php_present, fetcher=net)
    need = tb.needs(["wordpress", "wp_sqlite", "wp_cli"])
    assert need["missing"] == ["wordpress", "wp_sqlite", "wp_cli"] and need["unapproved"] == need["missing"]
    with pytest.raises(ToolNotApproved):
        tb.ensure(["wordpress"])                                   # never without the owner's OK
    job = tb.run_job_now(tb.request_install(["wp_sqlite", "wp_cli"], "zak")["id"])
    assert job["state"] == "done", job["error"]
    assert tb.needs(["wordpress", "wp_sqlite", "wp_cli"])["ready"]
    status = {t["id"]: t for t in tb.status()["tools"]}
    assert status["wordpress"]["version"] == "7.1.3" and status["wordpress"]["approved_by"] == "zak"
    assert status["php"]["approved"] and not status["php"]["installed_by_hood"]     # was already there
    assert {"wordpress.org", "api.wordpress.org", "downloads.wordpress.org", "raw.githubusercontent.com"} <= \
        {r["host"] for r in fw.list_rules()}                       # the owner's OK allowed the official hosts
    # Removed later: HOOD may reinstall it without asking? No: removing withdraws the approval.
    tb.remove("wordpress", "zak")
    assert not tb.approved("wordpress")
    with pytest.raises(ToolNotApproved):
        tb.ensure(["wordpress"])
    # An approved tool that went missing is reinstalled without asking.
    tb.run_job_now(tb.request_install(["wordpress"], "zak")["id"])
    tb.path("wp_cli").unlink()
    job = tb.ensure(["wp_cli"], actor="hood")
    assert tb.run_job_now(job["id"])["state"] == "done" and tb.path("wp_cli").is_file()


def test_download_that_does_not_match_the_checksum_is_refused(tmp_path, linux):
    net = FakeNet(sha1="0" * 40)
    tb = Toolbox(tmp_path, runner=php_present, fetcher=net)
    job = tb.run_job_now(tb.request_install(["wordpress"], "zak")["id"])
    assert job["state"] == "failed" and "checksum" in job["error"]
    assert not tb.path("wordpress").exists()


def test_plugin_files_must_match_wordpress_org_checksums(tmp_path, linux):
    files = {"sqlite-database-integration/load.php": "<?php /* Version: 3.1.0 */",
             "sqlite-database-integration/db.copy": "<?php"}
    tampered = _plugin(files, sums={"load.php": {"sha256": "0" * 64}, "db.copy": {"sha256": "0" * 64}})
    tb = Toolbox(tmp_path, runner=php_present, fetcher=FakeNet(plugin=tampered))
    job = tb.run_job_now(tb.request_install(["wp_sqlite"], "zak")["id"])
    assert job["state"] == "failed" and "checksum" in job["error"] and not tb.path("wp_sqlite").exists()
    unlisted = _plugin({**files, "sqlite-database-integration/extra.php": "<?php evil();"},
                       sums={"load.php": {"sha256": hashlib.sha256(files["sqlite-database-integration/load.php"]
                                                                   .encode()).hexdigest()},
                             "db.copy": {"sha256": hashlib.sha256(b"<?php").hexdigest()}})
    tb2 = Toolbox(tmp_path / "b", runner=php_present, fetcher=FakeNet(plugin=unlisted))
    job = tb2.run_job_now(tb2.request_install(["wp_sqlite"], "zak")["id"])
    assert job["state"] == "failed" and "extra.php" in job["error"]


def test_archives_cannot_escape_the_tools_folder(tmp_path, linux):
    evil = _wp_tarball(extra={"../../escaped.txt": "x"})
    tb = Toolbox(tmp_path, runner=php_present, fetcher=FakeNet(tarball=evil))
    job = tb.run_job_now(tb.request_install(["wordpress"], "zak")["id"])
    assert job["state"] == "failed"
    assert not (tmp_path.parent / "escaped.txt").exists() and not (tmp_path / "escaped.txt").exists()


def test_windows_asks_for_wsl2_instead_of_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(Toolbox, "platform_problem",
                        lambda self: "Installs happen only inside WSL2 (your setting)...")
    tb = Toolbox(tmp_path, runner=php_present, fetcher=FakeNet())
    need = tb.needs(["wordpress"])
    assert need["problem"].startswith("Installs happen only inside WSL2")
    with pytest.raises(ToolUnavailable):
        tb.request_install(["wordpress"], "zak")


def test_system_packages_go_through_the_allowlisted_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(Toolbox, "platform_problem", lambda self: None)
    helper = tmp_path / "hood-pkg"
    helper.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOOD_PKG_HELPER", str(helper))
    installed = set()
    monkeypatch.setattr(svc.shutil, "which", lambda name: "/usr/bin/" + name if name in installed else None)
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        if argv[-1] == "check":
            return 0, "hood-pkg ready"
        if "install" in argv:
            installed.add("sqlite3")
            return 0, "Setting up sqlite3"
        return 0, "3.45.1"
    tb = Toolbox(tmp_path / "data", runner=runner, fetcher=FakeNet())
    job = tb.run_job_now(tb.request_install(["sqlite3"], "zak")["id"])
    assert job["state"] == "done", job["error"]
    install = next(c for c in calls if "install" in c)
    assert install[-3:] == [str(helper), "install", "sqlite3"] or install[-2:] == ["install", "sqlite3"]
    assert next(t for t in tb.status()["tools"] if t["id"] == "sqlite3")["installed_by_hood"]
    # Not switched on yet -> a clear instruction, not a silent failure.
    monkeypatch.setenv("HOOD_PKG_HELPER", str(tmp_path / "missing"))
    assert "enable_installs.sh" in Toolbox(tmp_path / "x", runner=runner).helper_problem()


def test_hood_only_removes_what_it_installed(tmp_path, linux):
    tb = Toolbox(tmp_path, runner=php_present, fetcher=FakeNet())
    tb.request_install(["php"], "zak")
    with pytest.raises(PermissionError, match="already on this computer"):
        tb.remove("php", "zak")
