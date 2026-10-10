"""Backup / verify / restore rehearsal for HOOD_DATA_DIR (local, not staging evidence)."""
import importlib.util
import zipfile
from pathlib import Path

import pytest

from services.agents import AgentEngine
from services.auth.auth_service import AuthenticationService
from tests.agents.scripted_provider import ScriptedModel
from tests.agents.test_agent_engine import OBJECTIVE, OWNER

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("hood_backup", ROOT / "scripts" / "hood_backup.py")
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)


def test_backup_restore_round_trip_and_tamper_detection(tmp_path):
    data = tmp_path / "data"
    auth = AuthenticationService(db_path=data / "auth.db")
    auth.initialize_root_owner("owner", "Owner", "OwnerPassword123!")
    engine = AgentEngine(data / "agents", invoke=ScriptedModel(), allow_simulated=True)
    mid = engine.create_mission(OWNER, OBJECTIVE)["mission_id"]

    archive = hb.backup(data, tmp_path / "backups")
    with zipfile.ZipFile(archive) as zf:
        assert "agents/.receipt_key" not in zf.namelist(), "keys are excluded by default"
    assert hb.verify(archive)["files"]

    restored = tmp_path / "restored"
    hb.restore(archive, restored)
    assert AuthenticationService(db_path=restored / "auth.db").is_initialized()
    # The receipt key is not in the backup, so restored receipts must be re-keyed by the operator;
    # mission data itself is intact.
    import sqlite3
    with sqlite3.connect(restored / "agents" / "agent_engine.sqlite3") as db:
        assert db.execute("SELECT state FROM missions WHERE id=?", (mid,)).fetchone()[0] == "AWAITING_PLAN_APPROVAL"

    with pytest.raises(FileExistsError):
        hb.restore(archive, restored)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tampered, "w") as dst:
        for item in src.infolist():
            payload = src.read(item.filename)
            if item.filename == "auth.db":
                payload = payload[:-1] + bytes([payload[-1] ^ 1])
            dst.writestr(item, payload)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        hb.verify(tampered)
    with pytest.raises(ValueError):
        hb.restore(tampered, tmp_path / "never")
    assert not (tmp_path / "never").exists()


def test_backup_with_keys_restores_working_receipts(tmp_path):
    data = tmp_path / "data"
    engine = AgentEngine(data / "agents", invoke=ScriptedModel(), allow_simulated=True)
    mid = engine.create_mission(OWNER, OBJECTIVE)["mission_id"]
    archive = hb.backup(data, tmp_path / "b", include_keys=True)
    hb.restore(archive, tmp_path / "r")
    again = AgentEngine(tmp_path / "r" / "agents", invoke=ScriptedModel(), allow_simulated=True)
    assert again.verify_receipts(OWNER, mid)["valid"] is True
