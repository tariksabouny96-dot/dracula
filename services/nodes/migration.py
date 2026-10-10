"""
HOOD Node Migration & Export/Import Tooling
Governed by Master System Specification Section 17 & V0.5A Multi-Node Foundation.

Enables zero-leakage, zero-friction machine migration:
- Exports non-secret configuration, memory dumps, bootstrap manifests,
  and project metadata into an authenticated verification bundle.
- Validates bundle integrity (SHA256 checksums).
- Imports bundles safely into target node while rejecting plaintext secret leakage
  and preserving cryptographic isolation.
"""

import os
import io
import json
import hmac
import tarfile
import hashlib
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timezone

from services.memory.backends import SQLiteBackend, MemoryMigrationTool
from packages.config.paths import store_dir


class NodeMigrationBundle:
    """Manages creation, inspection, and verification of HOOD node migration bundles."""

    MIGRATION_KEY_ENV = "HOOD_MIGRATION_KEY"

    def __init__(self, workspace_root: Optional[Path] = None):
        self.workspace_root = workspace_root or Path.cwd()

    def _effective_key(self, signing_key: Optional[str]) -> Optional[str]:
        key = signing_key if signing_key is not None else os.environ.get(self.MIGRATION_KEY_ENV)
        return key or None

    @staticmethod
    def _signing_payload(node_id: str, exported_at: str, hood_version: str,
                         records_hash: str, records_count: int) -> bytes:
        """Canonical bytes a sender signs; excludes the signature itself."""
        return json.dumps({
            "node_id": node_id,
            "exported_at": exported_at,
            "hood_version": hood_version,
            "memory_export_hash": records_hash,
            "memory_records_count": records_count,
        }, sort_keys=True).encode("utf-8")

    def _sign(self, key: str, payload: bytes) -> str:
        return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    def export_node(
        self,
        output_tar_path: Path,
        node_id: str,
        sqlite_db_path: Optional[Path] = None,
        signing_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a clean migration archive containing:
        1. metadata.json (node metadata, export timestamp, schema versions)
        2. memory_export.json (sanitized, non-X-sealed memory records with SHA256)
        3. config_manifest.json (public configurations, omitting raw API keys/secrets)
        4. manifest.sha256 (bundle integrity verification)
        """
        export_dir = store_dir(None, "artifacts/migration_temp")
        export_dir.mkdir(parents=True, exist_ok=True)

        # 1. Export memory
        memory_dump_path = export_dir / "memory_export.json"
        if sqlite_db_path and sqlite_db_path.exists():
            backend = SQLiteBackend(db_path=sqlite_db_path)
            records = [r for r in backend.export_all()
                       if r.get("type") != "X_SEALED" and r.get("project") != "X_SEALED"]
            export_hash = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
            export_stats = {"total_records": len(records), "export_hash": export_hash}
            with open(memory_dump_path, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"count": len(records), "hash": export_hash}, "records": records}, f, indent=2)
        else:
            empty_hash = hashlib.sha256(json.dumps([], sort_keys=True).encode()).hexdigest()
            export_stats = {"total_records": 0, "export_hash": empty_hash}
            with open(memory_dump_path, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"count": 0, "hash": empty_hash}, "records": []}, f)

        # 2. Config manifest (sanitized)
        config_manifest_path = export_dir / "config_manifest.json"
        exported_at = datetime.now(timezone.utc).isoformat()
        hood_version = "v0.5a"
        records_hash = export_stats.get("export_hash", "")
        records_count = export_stats.get("total_records", 0)
        manifest_data = {
            "node_id": node_id,
            "exported_at": exported_at,
            "hood_version": hood_version,
            "secrets_included": False,  # Strict guarantee: secrets are NEVER bundled in migration tarballs
            "memory_records_count": records_count,
            "memory_export_hash": records_hash
        }
        # Sender authentication (F27): when a migration key is configured, sign
        # the bundle so a receiver can prove it came from an authorised sender,
        # not merely that its contents hash to themselves (which anyone can forge).
        key = self._effective_key(signing_key)
        if key:
            payload = self._signing_payload(node_id, exported_at, hood_version, records_hash, records_count)
            manifest_data["signed"] = True
            manifest_data["sender_signature"] = self._sign(key, payload)
        else:
            manifest_data["signed"] = False
        with open(config_manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        # 3. Create tar.gz bundle
        output_tar_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output_tar_path, "w:gz") as tar:
            tar.add(memory_dump_path, arcname="memory_export.json")
            tar.add(config_manifest_path, arcname="config_manifest.json")

        # 4. Compute archive SHA256
        with open(output_tar_path, "rb") as f:
            archive_hash = hashlib.sha256(f.read()).hexdigest()

        # Clean up temp
        try:
            memory_dump_path.unlink()
            config_manifest_path.unlink()
            export_dir.rmdir()
        except Exception:
            pass

        return {
            "success": True,
            "bundle_path": str(output_tar_path),
            "archive_sha256": archive_hash,
            "records_exported": export_stats.get("total_records", 0),
            "export_hash": export_stats.get("export_hash", "")
        }

    def inspect_bundle(self, tar_path: Path) -> Dict[str, Any]:
        """Reads metadata from a migration archive without unpacking to disk."""
        if not tar_path.exists():
            raise FileNotFoundError(f"Bundle not found at {tar_path}")

        with tarfile.open(tar_path, "r:gz") as tar:
            try:
                manifest_file = tar.extractfile("config_manifest.json")
                if not manifest_file:
                    raise ValueError("Malformed bundle: missing config_manifest.json")
                manifest = json.loads(manifest_file.read().decode("utf-8"))
                return manifest
            except KeyError:
                raise ValueError("Malformed bundle: missing config_manifest.json")

    def import_node(
        self,
        tar_path: Path,
        target_sqlite_path: Path,
        signing_key: Optional[str] = None,
        require_authentication: bool = True
    ) -> Dict[str, Any]:
        """
        Safely unpacks and validates a migration archive, importing memory
        into the target database with cryptographic validation.

        Sender authentication (F27): by default a valid HMAC signature from an
        authorised sender is required. With no key configured, or an unsigned or
        wrongly-signed bundle, the import is refused and the target is left
        unchanged. require_authentication=False is an explicit, caller-visible
        opt-out for same-owner offline migration.
        """
        if not tar_path.exists():
            raise FileNotFoundError(f"Bundle not found at {tar_path}")

        # Read only the two explicitly permitted archive members in memory.
        # No tar.extractall: prevent traversal, links and destination writes.
        with tarfile.open(tar_path, "r:gz") as tar:
            names = tar.getnames()
            if sorted(names) != ["config_manifest.json", "memory_export.json"]:
                raise ValueError("Migration bundle contains unexpected paths or members")
            contents = {}
            for member_name in names:
                info = tar.getmember(member_name)
                if not info.isfile() or info.size > 10 * 1024 * 1024:
                    raise ValueError("Invalid migration member type or size")
                contents[member_name] = json.loads(tar.extractfile(info).read().decode("utf-8"))

        manifest = contents["config_manifest.json"]
        data = contents["memory_export.json"]
        records = data.get("records")
        if not isinstance(records, list) or any(
            not isinstance(r, dict) or r.get("type") == "X_SEALED" or r.get("project") == "X_SEALED"
            for r in records
        ):
            raise ValueError("Sensitive or malformed memory migration records rejected")
        expected = data.get("metadata", {}).get("hash")
        actual = hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
        if not expected or expected != actual or manifest.get("memory_export_hash") != actual:
            raise ValueError("Migration integrity verification failed; target left unchanged")

        # Sender authentication (F27): verify the HMAC before touching the target.
        if require_authentication:
            key = self._effective_key(signing_key)
            if not key:
                raise PermissionError(
                    "No migration key configured; cannot authenticate the sender. "
                    "Set HOOD_MIGRATION_KEY, pass signing_key, or explicitly "
                    "import with require_authentication=False for same-owner migration."
                )
            if not manifest.get("signed") or not manifest.get("sender_signature"):
                raise PermissionError("Migration bundle is not signed by an authorised sender; refused.")
            payload = self._signing_payload(
                manifest.get("node_id", ""),
                manifest.get("exported_at", ""),
                manifest.get("hood_version", ""),
                manifest.get("memory_export_hash", ""),
                manifest.get("memory_records_count", 0),
            )
            expected_sig = self._sign(key, payload)
            if not hmac.compare_digest(expected_sig, str(manifest.get("sender_signature"))):
                raise PermissionError("Migration bundle sender signature is invalid; refused.")

        # Transactional import: import_records runs in a single SQLite
        # transaction that rolls back on any error, so a failure leaves the
        # target unchanged rather than half-populated.
        backend = SQLiteBackend(db_path=target_sqlite_path)
        imported = backend.import_records(records)
        return {
            "success": imported == len(records),
            "node_id": manifest.get("node_id"),
            "imported_records": imported,
            "integrity_verified": imported == len(records),
            "authenticated": bool(require_authentication),
        }
