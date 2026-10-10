"""Security batch 1/S2: every persistent store lives under HOOD_DATA_DIR, old stores are carried over
once, and a backup of the data folder restores a working HOOD (owner, vault key, audit, missions,
firewall, tool approvals, self-development) without leaking keys or bulk."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from packages.config import paths

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hood_backup", ROOT / "scripts" / "hood_backup.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)

BOOT = r'''
import sys
sys.argv = ["hood_cli.py"]
import hood_cli
from ui.server import JarvisServer
from services.auth.auth_service import AuthenticationService
rt = hood_cli.HoodSystemRuntime()
auth = AuthenticationService(db_path=hood_cli._auth_db_path(rt))
srv = JarvisServer(interaction_service=rt.interaction_service, emergency_stop=rt.emergency_stop, runtime=rt,
                   port=0, auth_service=auth)
srv.start()
auth.setup_code()
auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
rt.vault.set_secret("gemini", "api_key", "k" * 39)
srv.stop()
print("BOOTED")
'''


def _files(folder: Path):
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} if folder.exists() else set()


def test_a_running_hood_writes_only_into_its_data_folder(tmp_path):
    cwd, home, data = tmp_path / "cwd", tmp_path / "home", tmp_path / "data"
    for d in (cwd, home):
        d.mkdir()
    repo_artifacts_before = _files(ROOT / "artifacts")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "HOOD_DATA_DIR": str(data),
           "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", "HOOD_SKIP_LEGACY_MIGRATION": "1"}
    proc = subprocess.run([sys.executable, "-c", BOOT], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    assert "BOOTED" in proc.stdout, proc.stderr[-3000:]
    assert _files(cwd) == set(), "nothing may be written relative to the folder HOOD starts in"
    assert _files(home) == set(), "nothing may go to ~/.hood when HOOD_DATA_DIR is set"
    assert _files(ROOT / "artifacts") - repo_artifacts_before == set(), "nothing in the code folder"
    stored = _files(data)
    for store in ("auth.db", "hood_data.db", "vault.enc", ".vault_key", "nova21/missions.sqlite3",
                  "agents/agent_engine.sqlite3", "firewall/rules.json", "conversations.sqlite3",
                  "economic_engine.db", "nodes/node_registry.json", "self_dev/self_dev.db"):
        assert store in stored, store


def _sqlite(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (v TEXT)")
    db.executemany("INSERT INTO t VALUES (?)", [(r,) for r in rows])
    db.commit()
    return db                      # left open: the WAL is not checkpointed into the main file


def test_old_stores_are_copied_once_and_never_overwrite(tmp_path, monkeypatch):
    old, home, data = tmp_path / "old", tmp_path / "home", tmp_path / "data"
    monkeypatch.setenv("HOOD_DATA_DIR", str(data))
    monkeypatch.delenv("HOOD_VAULT_KEY", raising=False)
    from packages.auth.vault import SecretVault
    legacy_vault = SecretVault(old / "artifacts" / "vault.enc")
    legacy_vault.set_secret("gemini", "api_key", "g" * 39)
    conn = _sqlite(old / "artifacts" / "hood_data.db", ["audit-1", "audit-2"])
    (old / "artifacts" / "nodes").mkdir(parents=True)
    (old / "artifacts" / "nodes" / "node_registry.json").write_text('{"n": 1}')
    (old / "artifacts" / "checkpoints").mkdir()
    (old / "artifacts" / "checkpoints" / "chk.bak").write_text("restore point")
    (old / "artifacts" / "dev_logs").mkdir()
    (old / "artifacts" / "dev_logs" / "x.log").write_text("scratch")
    _sqlite(home / ".hood" / "nova21" / "missions.sqlite3", ["m1"]).close()
    (data / "auth.db").parent.mkdir(parents=True)
    (data / "auth.db").write_text("already here")           # newer store in the data folder wins
    (old / "artifacts" / "auth.db").write_text("old")

    moved = paths.migrate_legacy_data(roots=[old], home=home)
    conn.close()
    assert {n.relative_to(data).as_posix() for _, n in moved} == {
        "vault.enc", "hood_data.db", "nodes/node_registry.json", "checkpoints", "nova21"}
    assert (data / "auth.db").read_text() == "already here"
    assert not (data / "dev_logs").exists()                  # scratch is not carried over
    copied = sqlite3.connect(data / "hood_data.db")
    assert [r[0] for r in copied.execute("SELECT v FROM t")] == ["audit-1", "audit-2"]   # WAL included
    copied.close()
    assert SecretVault(data / "vault.enc").get_secret("SECRET://gemini/api_key") == "g" * 39
    if os.name == "posix":
        assert (data / ".vault_key").stat().st_mode & 0o077 == 0
    assert (old / "artifacts" / "hood_data.db").exists()      # old copy left for the owner
    assert paths.migrate_legacy_data(roots=[old], home=home) == []      # once
    monkeypatch.setenv("HOOD_SKIP_LEGACY_MIGRATION", "1")
    assert paths.migrate_legacy_data() == []                  # the test suite never reads real data


def test_relative_store_settings_resolve_into_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "d"))
    assert paths.store_path(None, "artifacts/hood_data.db") == tmp_path / "d" / "hood_data.db"
    assert paths.store_path("artifacts/custom/x.db", "unused") == tmp_path / "d" / "custom" / "x.db"
    assert paths.store_path("db/y.db", "unused") == tmp_path / "d" / "db" / "y.db"
    assert paths.store_path(tmp_path / "abs.db", "unused") == tmp_path / "abs.db"
    from services.audit.service import AuditService
    from services.memory.service import MemoryService
    from services.auth.auth_service import AuthenticationService
    assert AuditService().db_path == tmp_path / "d" / "hood_data.db"
    assert MemoryService(Path("artifacts/hood_data.db")).db_path == tmp_path / "d" / "hood_data.db"
    assert AuthenticationService().db_path == tmp_path / "d" / "auth.db"


def _populate(data: Path):
    """A HOOD with an owner, a saved key, audit/memory rows, missions, a firewall rule, a tool
    approval, a self-development proposal and scratch bulk that must stay out of the backup."""
    from packages.auth.vault import SecretVault
    from packages.contracts import AuditEvent
    from services.agents import AgentEngine
    from services.audit.service import AuditService
    from services.auth.auth_service import AuthenticationService
    from services.evolution.self_development import SelfDevelopmentController
    from services.firewall.policy import NetworkFirewall
    from services.operations.mission_service import MissionService
    from tests.agents.scripted_provider import ScriptedModel
    from tests.agents.test_agent_engine import OBJECTIVE, OWNER
    auth = AuthenticationService(db_path=data / "auth.db")
    auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    SecretVault(data / "vault.enc").set_secret("gemini", "api_key", "s" * 39)
    AuditService(db_path=data / "hood_data.db").record_event(AuditEvent(
        actor="owner", action="TEST", target="backup", policy_decision="ALLOW", task_id="bk1"))
    mid = AgentEngine(data / "agents", invoke=ScriptedModel(), allow_simulated=True).create_mission(
        OWNER, OBJECTIVE)["mission_id"]
    MissionService(data / "nova21").create(OWNER, "Legacy mission", "Kept in backups")
    NetworkFirewall(data / "firewall").allow("example.org", [443], note="test", added_by="owner",
                                             is_root_owner=True)
    (data / "tools").mkdir()
    (data / "tools" / "state.json").write_text(json.dumps({"tools": {"wordpress": {"approved_by": "owner"}}}))
    (data / "tools" / "wordpress").mkdir()
    (data / "tools" / "wordpress" / "index.php").write_text("<?php // rebuildable")
    (data / "wsl" / "HOOD").mkdir(parents=True)
    (data / "wsl" / "HOOD" / "ext4.vhdx").write_bytes(b"\0" * 4096)
    (data / "agents" / "runtime" / mid).mkdir(parents=True)
    (data / "agents" / "runtime" / mid / "copy.php").write_text("scratch")
    (data / "owner_setup_code.txt").write_text("ABCD-EFGH-JKLM")
    (data / "artifacts").mkdir()
    (data / "artifacts" / ".token_key").write_bytes(b"k" * 32)
    proposal = SelfDevelopmentController(workspace_root=ROOT, data_dir=data / "self_dev").propose(
        "docs/README_SELFDEV_TEST.md", "# test\n", "backup coverage")
    return mid, proposal["proposal_id"]


def test_a_backup_restores_a_complete_working_hood(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("HOOD_DATA_DIR", str(data))
    monkeypatch.delenv("HOOD_VAULT_KEY", raising=False)
    mid, pid = _populate(data)

    plain = hb.backup(data, tmp_path / "b1")                  # default: no keys
    with zipfile.ZipFile(plain) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("HOOD_BACKUP_MANIFEST.json"))
    for secret in (".vault_key", "agents/.receipt_key", "artifacts/.token_key"):
        assert secret not in names and secret in manifest["skipped_keys"]
    for bulk in ("wsl/HOOD/ext4.vhdx", "tools/wordpress/index.php", f"agents/runtime/{mid}/copy.php",
                 "owner_setup_code.txt"):
        assert bulk not in names and bulk in manifest["excluded"]
    assert "tools/state.json" in names and "vault.enc" in names

    full = hb.backup(data, tmp_path / "b2", include_keys=True)
    restored = tmp_path / "restored"
    hb.restore(full, restored)
    monkeypatch.setenv("HOOD_DATA_DIR", str(restored))
    from packages.auth.vault import SecretVault
    from services.agents import AgentEngine
    from services.audit.service import AuditService
    from services.auth.auth_service import AuthenticationService
    from services.evolution.self_development import SelfDevelopmentController
    from services.firewall.policy import NetworkFirewall
    from services.operations.mission_service import MissionService
    from tests.agents.scripted_provider import ScriptedModel
    from tests.agents.test_agent_engine import OWNER
    assert AuthenticationService(db_path=restored / "auth.db").authenticate("owner", "OwnerPassword123!")
    assert SecretVault(restored / "vault.enc").get_secret("SECRET://gemini/api_key") == "s" * 39
    assert AuditService(db_path=restored / "hood_data.db").get_events_for_task("bk1")
    engine = AgentEngine(restored / "agents", invoke=ScriptedModel(), allow_simulated=True)
    assert engine.status(OWNER, mid)["state"] == "AWAITING_PLAN_APPROVAL"
    assert engine.verify_receipts(OWNER, mid)["valid"] is True
    assert MissionService(restored / "nova21").list(OWNER)
    assert any(r["host"] == "example.org" for r in NetworkFirewall(restored / "firewall").list_rules())
    assert json.loads((restored / "tools" / "state.json").read_text())["tools"]["wordpress"]["approved_by"] == "owner"
    assert SelfDevelopmentController(workspace_root=ROOT, data_dir=restored / "self_dev").get(pid)["state"] \
        == "AWAITING_APPROVAL"
    if os.name == "posix":
        assert all((p.stat().st_mode & 0o077) == 0 for p in restored.rglob("*") if p.is_file())


def test_backup_never_includes_the_package_helper_or_writes_outside_its_target(tmp_path):
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "scripts/wsl/" in dockerignore                     # the passwordless helper never ships
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "chown -R hood:hood /data" in dockerfile and "/data /app" not in dockerfile
    assert "HOOD_ENABLE_PKG_HELPER=0" in dockerfile
