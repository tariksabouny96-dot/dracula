"""
HOOD Secret Vault & Reference Management
Enforces secret references (SECRET://provider/key) instead of plaintext credential storage.
Governed by Master System Specification Section 11.2 & Build Instructions Section 8.
"""

import os
import json
import base64
from pathlib import Path

from packages.config.paths import store_path
from typing import Dict, List, Optional, Union
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from packages.contracts import SecretReference


class SecretVault:
    """Secure encrypted vault for credential storage using reference-based retrieval."""

    def __init__(self, vault_path: Optional[Path] = None, master_key: Optional[bytes] = None):
        # A relative vault path lives in HOOD's data folder (HOOD_DATA_DIR), with the rest of the
        # owner's data: on the container volume and in backups, never inside the code folder
        # (an old vault there is copied over at start-up, see packages/config/paths.py).
        self.vault_path = store_path(vault_path, "artifacts/vault.enc")
        self._key = master_key or self._get_or_create_master_key()
        self._fernet = Fernet(self._key)
        self._secrets: Dict[str, str] = {}
        self._metadata: Dict[str, dict] = {}
        self._load()

    def _get_or_create_master_key(self) -> bytes:
        # Preferred: key supplied by the OS secret store / service manager via env,
        # so the key never sits beside the ciphertext.
        env_key = os.environ.get("HOOD_VAULT_KEY")
        if env_key:
            return env_key.strip().encode("ascii")
        key_file = self.vault_path.parent / ".vault_key"
        if key_file.exists():
            if os.name == "posix" and key_file.stat().st_mode & 0o077:
                raise PermissionError(
                    f"Vault key {key_file} is readable by other users; run chmod 600 before use")
            return key_file.read_bytes().strip()
        
        # Never derive credentials from host names or static salts.
        key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(str(key_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(key)
        return key

    def _load(self) -> None:
        if not self.vault_path.exists():
            return
        try:
            encrypted_data = self.vault_path.read_bytes()
            decrypted = self._fernet.decrypt(encrypted_data)
            payload = json.loads(decrypted.decode("utf-8"))
            self._secrets = payload.get("secrets", {})
            self._metadata = payload.get("metadata", {})
        except (InvalidToken, ValueError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
            # Never treat ciphertext corruption or incorrect keys as an empty vault.
            raise ValueError("Vault cannot be decrypted; preserve the encrypted file and its key") from exc

    def _save(self) -> None:
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "secrets": self._secrets,
            "metadata": self._metadata
        }
        raw_json = json.dumps(payload).encode("utf-8")
        encrypted = self._fernet.encrypt(raw_json)
        import tempfile
        import os
        fd, temp_name = tempfile.mkstemp(prefix=".hood_vault_", dir=str(self.vault_path.parent))
        try:
            os.chmod(temp_name, 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(encrypted)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp_name, self.vault_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def rotate_key(self) -> bytes:
        """Re-encrypt every secret under a fresh key; returns the new key.

        When the key lives in the key file it is replaced atomically after the
        vault is rewritten; with HOOD_VAULT_KEY the caller must store the new key.
        """
        new_key = Fernet.generate_key()
        self._key, self._fernet = new_key, Fernet(new_key)
        self._save()
        if not os.environ.get("HOOD_VAULT_KEY"):
            key_file = self.vault_path.parent / ".vault_key"
            tmp = key_file.with_suffix(".new")
            with os.fdopen(os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as handle:
                handle.write(new_key)
            os.replace(tmp, key_file)
        return new_key

    @staticmethod
    def normalize_uri(uri_or_provider: str, key_name: Optional[str] = None) -> str:
        if key_name:
            provider = uri_or_provider.strip("/").removeprefix("SECRET://")
            return f"SECRET://{provider}/{key_name}"
        if not uri_or_provider.startswith("SECRET://"):
            parts = uri_or_provider.strip("/").split("/")
            if len(parts) >= 2:
                return f"SECRET://{parts[0]}/{parts[1]}"
            return f"SECRET://default/{parts[0]}"
        return uri_or_provider

    def set_secret(
        self,
        provider: str,
        key_name: str,
        value: str,
        description: Optional[str] = None
    ) -> SecretReference:
        uri = self.normalize_uri(provider, key_name)
        self._secrets[uri] = value
        self._metadata[uri] = {
            "provider": provider,
            "key_name": key_name,
            "description": description or ""
        }
        self._save()
        return SecretReference(uri=uri, provider=provider, key_name=key_name, description=description)

    def get_secret(self, ref: Union[str, SecretReference]) -> Optional[str]:
        uri = ref.uri if isinstance(ref, SecretReference) else self.normalize_uri(ref)
        return self._secrets.get(uri)

    def has_secret(self, ref: Union[str, SecretReference]) -> bool:
        uri = ref.uri if isinstance(ref, SecretReference) else self.normalize_uri(ref)
        return uri in self._secrets

    def delete_secret(self, ref: Union[str, SecretReference]) -> bool:
        uri = ref.uri if isinstance(ref, SecretReference) else self.normalize_uri(ref)
        if uri in self._secrets:
            del self._secrets[uri]
            self._metadata.pop(uri, None)
            self._save()
            return True
        return False

    def list_references(self) -> List[SecretReference]:
        refs = []
        for uri, meta in self._metadata.items():
            refs.append(SecretReference(
                uri=uri,
                provider=meta.get("provider", "unknown"),
                key_name=meta.get("key_name", "unknown"),
                description=meta.get("description", "")
            ))
        return refs
