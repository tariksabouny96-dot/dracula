"""The artifact registry must work on Windows (no POSIX mode bits, no O_NOFOLLOW)
while keeping its protections. Regression for the owner's Windows run, where the
exports self-test failed with ArtifactError on every start after the first."""
import os

import pytest

from services.artifacts import registry as reg
from services.artifacts.registry import ArtifactError, ArtifactRegistry

BINARY = bytes(range(256)) * 4  # contains \n, \r and \x1a: text mode would corrupt it


def test_restart_reuses_key_file_on_windows(tmp_path, monkeypatch):
    monkeypatch.delenv(reg.TOKEN_ENV, raising=False)
    ArtifactRegistry(tmp_path)                      # first start creates the key file
    os.chmod(tmp_path / ".token_key", 0o666)        # what Windows reports for any file
    monkeypatch.setattr(reg, "_POSIX", False)  # Windows: no POSIX mode bits
    ArtifactRegistry(tmp_path)                      # second start must not refuse it


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_posix_still_refuses_a_group_readable_key(tmp_path, monkeypatch):
    monkeypatch.delenv(reg.TOKEN_ENV, raising=False)
    ArtifactRegistry(tmp_path)
    os.chmod(tmp_path / ".token_key", 0o644)
    with pytest.raises(ArtifactError, match="group/other-accessible"):
        ArtifactRegistry(tmp_path)


def test_binary_round_trip_without_nofollow(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "_NOFOLLOW", 0)        # Windows has no O_NOFOLLOW
    r = ArtifactRegistry(tmp_path, signing_key=b"k" * 32)
    art = r.store("owner", "x.bin", "application/octet-stream", BINARY)
    token = r.mint_token("owner", art["artifact_id"])["token"]
    name, mime, data = r.redeem(token, expected_owner="owner")
    assert data == BINARY


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_symlinked_blob_is_refused_without_nofollow(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "_NOFOLLOW", 0)
    target = tmp_path / "secret.txt"
    target.write_text("not an artifact")
    link = tmp_path / "blob"
    os.symlink(target, link)
    with pytest.raises(ArtifactError, match="symlink"):
        ArtifactRegistry._read_blob(link)
