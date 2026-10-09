"""
HOOD Development Executor - Reversible Code Modifier & Git Safety Guard
Enforces safe modification lifecycle:
1. UNDERSTAND -> 2. PLAN -> 3. CHECKPOINT / BRANCH -> 4. EDIT -> 5. VERIFY -> 6. ROLLBACK IF FAILED.
Protects sensitive files (.env, vault, credentials) and guarantees reversible operations.
Governed by Master System Specification Sections 2, 7, 9, 15 & V0.3 Development Executor Spec.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class CodeEditResult(BaseModel):
    success: bool
    target_file: str
    checkpoint_ref: Optional[str] = None
    git_branch: Optional[str] = None
    diff: Optional[str] = None
    action_type: str  # "modify", "created", "rolled_back"
    error: Optional[str] = None


class CodeModifier:
    """Safely applies targeted, reversible code modifications with backup and Git checkpoints."""

    BLOCKED_PATTERNS = [
        ".env", "vault.enc", "id_rsa", "secret", "credentials", "x_sealed"
    ]

    def __init__(self, workspace_root: Path, checkpoint_dir: Optional[Path] = None):
        self.workspace_root = workspace_root.resolve()
        self.checkpoint_dir = (checkpoint_dir or (self.workspace_root / "artifacts" / "checkpoints")).resolve()
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.git_bin = "C:\\Program Files\\Git\\cmd\\git.exe" if os.path.exists("C:\\Program Files\\Git\\cmd\\git.exe") else "git"

    def _validate_path(self, target_rel_path: str) -> Path:
        from packages.security import confine_path, PathConfinementError
        try:
            resolved = confine_path(self.workspace_root, target_rel_path, label="target")
        except PathConfinementError:
            raise PermissionError(f"Path traversal blocked: '{target_rel_path}' is outside approved workspace root.") from None

        filename_lower = resolved.name.lower()
        if any(b in filename_lower for b in self.BLOCKED_PATTERNS):
            raise PermissionError(f"Modification blocked: '{resolved.name}' is a protected/sensitive secret file.")

        return resolved

    def create_file_checkpoint(self, target_path: Path) -> str:
        """Creates a timestamped local backup of a target file."""
        if not target_path.exists():
            return "NEW_FILE"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"chk_{target_path.name}_{ts}.bak"
        backup_file = self.checkpoint_dir / backup_name
        shutil.copy2(target_path, backup_file)
        return backup_name

    def restore_file_checkpoint(self, target_rel_path: str, checkpoint_ref: str) -> bool:
        """Restores a file to its checkpointed backup state."""
        target_path = self._validate_path(target_rel_path)
        if checkpoint_ref == "NEW_FILE":
            if target_path.exists():
                target_path.unlink()
            return True

        if (not isinstance(checkpoint_ref, str) or
            not checkpoint_ref.startswith(f"chk_{target_path.name}_") or
            Path(checkpoint_ref).name != checkpoint_ref):
            raise PermissionError("Checkpoint is not bound to this target file")
        backup_file = (self.checkpoint_dir / checkpoint_ref).resolve()
        if not backup_file.is_relative_to(self.checkpoint_dir) or not backup_file.is_file():
            raise FileNotFoundError(f"Checkpoint backup '{checkpoint_ref}' not found.")

        shutil.copy2(backup_file, target_path)
        return True

    def create_git_branch(self, branch_name: str) -> Dict[str, Any]:
        """Creates a dedicated development branch for safe reversible work."""
        res = subprocess.run(
            [self.git_bin, "checkout", "-b", branch_name],
            cwd=str(self.workspace_root),
            capture_output=True,
            text=True
        )
        return {
            "success": res.returncode == 0,
            "branch": branch_name,
            "output": res.stdout.strip() or res.stderr.strip()
        }

    def get_git_diff(self, target_rel_path: Optional[str] = None) -> str:
        """Retrieves exact Git diff for review."""
        cmd = [self.git_bin, "diff"]
        if target_rel_path:
            cmd.append(target_rel_path)
        res = subprocess.run(
            cmd,
            cwd=str(self.workspace_root),
            capture_output=True,
            text=True
        )
        return res.stdout.strip()

    def apply_targeted_edit(
        self,
        target_rel_path: str,
        content: str,
        is_patch_replace: bool = False,
        original_chunk: Optional[str] = None,
        replacement_chunk: Optional[str] = None
    ) -> CodeEditResult:
        """
        Applies a verified code modification after creating a restorable checkpoint.
        """
        target_path = self._validate_path(target_rel_path)
        checkpoint_ref = self.create_file_checkpoint(target_path)

        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if is_patch_replace and original_chunk is not None and replacement_chunk is not None:
                current_text = target_path.read_text(encoding="utf-8")
                if original_chunk not in current_text:
                    raise ValueError("Original target chunk not found in target file.")
                new_text = current_text.replace(original_chunk, replacement_chunk, 1)
                target_path.write_text(new_text, encoding="utf-8")
            else:
                target_path.write_text(content, encoding="utf-8")

            diff = self.get_git_diff(target_rel_path)

            return CodeEditResult(
                success=True,
                target_file=target_rel_path,
                checkpoint_ref=checkpoint_ref,
                diff=diff,
                action_type="modify" if checkpoint_ref != "NEW_FILE" else "created"
            )
        except Exception as e:
            # Automatic rollback on exception
            if checkpoint_ref != "NEW_FILE":
                self.restore_file_checkpoint(target_rel_path, checkpoint_ref)
            elif target_path.exists():
                target_path.unlink()

            return CodeEditResult(
                success=False,
                target_file=target_rel_path,
                checkpoint_ref=checkpoint_ref,
                action_type="rolled_back",
                error=str(e)
            )
