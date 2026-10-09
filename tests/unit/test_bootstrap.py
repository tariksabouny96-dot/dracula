import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

def test_bootstrap_idempotent():
    bootstrap_script = REPO_ROOT / "infra" / "bootstrap" / "bootstrap.py"
    # First run
    res1 = subprocess.run([sys.executable, str(bootstrap_script), "--role", "dev-temporary"], capture_output=True, text=True)
    assert res1.returncode == 0, f"Bootstrap run 1 failed: {res1.stderr}"

    # Second run immediately after (idempotency verification)
    res2 = subprocess.run([sys.executable, str(bootstrap_script), "--role", "dev-temporary"], capture_output=True, text=True)
    assert res2.returncode == 0, f"Bootstrap run 2 failed: {res2.stderr}"

    # Verify generated artifacts exist
    artifacts_dir = REPO_ROOT / "artifacts" / "bootstrap"
    assert (artifacts_dir / "machine-profile.json").exists()
    assert (artifacts_dir / "dependency-report.json").exists()
    assert (artifacts_dir / "bootstrap.log").exists()
    assert (artifacts_dir / "bootstrap-summary.md").exists()
