"""Consistent backup, verification and restore of Hood's runtime data directory.

    python scripts/hood_backup.py backup  --data-dir ~/.hood --out backups/
    python scripts/hood_backup.py verify  --archive backups/hood-backup-<ts>.zip
    python scripts/hood_backup.py restore --archive backups/hood-backup-<ts>.zip --data-dir ~/.hood-restored

SQLite databases are copied with the online backup API (consistent while Hood
runs); other files are copied as-is. A manifest records the SHA-256 of every
member and is checked before anything is restored. Restore refuses to write
into a non-empty directory, so it can never overwrite live data. Secrets
(vault key, receipt key, download-token key) are excluded unless --include-keys
is given; store those separately in the OS secret store (or use HOOD_VAULT_KEY /
HOOD_RECEIPT_KEY in the environment).

Since security batch 1 every persistent store lives under HOOD_DATA_DIR, so this
backup is complete. Rebuildable bulk is left out and listed in the manifest:
HOOD's Windows sandbox disk (wsl/HOOD), downloaded tool binaries (tools/, the
owner's tool approvals in tools/state.json ARE kept), per-run WordPress copies
(agents/runtime), logs, downloads, caches and the one-time setup code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

KEY_FILES = {".vault_key", ".receipt_key", ".token_key"}
SKIP_SUFFIXES = {"-wal", "-shm", ".tmp", ".migrating"}
SKIP_FILES = {"owner_setup_code.txt"}
# Rebuildable or scratch content (path prefixes relative to the data dir).
EXCLUDED_PREFIXES = ("wsl/HOOD/", "agents/runtime/", "dev_logs/", "downloads/", "browser_evidence/",
                     "desktop_evidence/", "disposable_cache/", "migration_temp/")
KEPT_IN_TOOLS = {"tools/state.json"}


def _excluded(rel: str) -> bool:
    if rel.startswith("tools/") and rel not in KEPT_IN_TOOLS:
        return True
    if rel.startswith("wsl/") and rel.endswith((".tar.gz", ".tar.zst")):
        return True
    return rel.startswith(EXCLUDED_PREFIXES)


def _is_sqlite(path: Path) -> bool:
    with path.open("rb") as fh:
        return fh.read(16) == b"SQLite format 3\x00"


def backup(data_dir: Path, out_dir: Path, include_keys: bool = False) -> Path:
    data_dir = data_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = out_dir / f"hood-backup-{stamp}.zip"
    manifest = {"format": 2, "created_at": stamp, "source": str(data_dir), "files": {},
                "keys_included": include_keys, "excluded": [], "skipped_keys": []}
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(data_dir.rglob("*")):
            if not path.is_file() or path.is_symlink() or any(str(path).endswith(s) for s in SKIP_SUFFIXES):
                continue
            rel = path.relative_to(data_dir).as_posix()
            if path.name in KEY_FILES and not include_keys:
                manifest["skipped_keys"].append(rel)
                continue
            if path.name in SKIP_FILES or _excluded(rel):
                manifest["excluded"].append(rel)
                continue
            if _is_sqlite(path):
                with tempfile.TemporaryDirectory() as tmp:
                    copy = Path(tmp) / "db"
                    src, dst = sqlite3.connect(path), sqlite3.connect(copy)
                    with dst:
                        src.backup(dst)
                    src.close()
                    dst.close()
                    data = copy.read_bytes()
            else:
                data = path.read_bytes()
            zf.writestr(rel, data)
            manifest["files"][rel] = hashlib.sha256(data).hexdigest()
        zf.writestr("HOOD_BACKUP_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True))
    return archive


def verify(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read("HOOD_BACKUP_MANIFEST.json"))
        names = set(zf.namelist()) - {"HOOD_BACKUP_MANIFEST.json"}
        if names != set(manifest["files"]):
            raise ValueError("Archive members do not match manifest")
        for rel, digest in manifest["files"].items():
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise ValueError(f"Unsafe member path {rel}")
            data = zf.read(rel)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"Checksum mismatch for {rel}")
            if rel.endswith((".sqlite3", ".db")):
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = Path(tmp) / "check.db"
                    db_path.write_bytes(data)
                    db = sqlite3.connect(db_path)
                    try:
                        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError(f"SQLite integrity check failed for {rel}")
                    finally:
                        db.close()
    return manifest


def restore(archive: Path, data_dir: Path) -> dict:
    manifest = verify(archive)
    data_dir = data_dir.resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise FileExistsError(f"Refusing to restore into non-empty {data_dir}")
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with zipfile.ZipFile(archive) as zf:
        for rel in manifest["files"]:
            target = (data_dir / rel).resolve()
            if not target.is_relative_to(data_dir):
                raise ValueError(f"Unsafe member path {rel}")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(zf.read(rel))
            target.chmod(0o600)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Hood data backup / verify / restore")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--data-dir", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--include-keys", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("--archive", type=Path, required=True)
    r = sub.add_parser("restore")
    r.add_argument("--archive", type=Path, required=True)
    r.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "backup":
        print(backup(args.data_dir, args.out, args.include_keys))
    elif args.cmd == "verify":
        print(json.dumps({"ok": True, "files": len(verify(args.archive)["files"])}))
    else:
        print(json.dumps({"restored": len(restore(args.archive, args.data_dir)["files"]), "to": str(args.data_dir)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
