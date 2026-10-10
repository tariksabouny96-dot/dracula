"""Startup must degrade gracefully: optional deps disable a feature, missing
required deps produce an actionable message instead of a traceback."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(code: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code, *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def test_browser_service_imports_without_playwright():
    # sys.modules[name] = None makes `import name` raise ImportError.
    proc = _run(
        "import sys\n"
        "sys.modules['playwright'] = None; sys.modules['playwright.sync_api'] = None\n"
        "import services.browser.browser_service as b\n"
        "assert b.PLAYWRIGHT_AVAILABLE is False\n"
        "print('ok')\n")
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_cli_reports_missing_required_package_instead_of_traceback():
    proc = _run(
        "import sys, runpy\n"
        "sys.modules['psutil'] = None\n"
        "sys.argv = ['hood_cli.py', 'status']\n"
        "runpy.run_path('hood_cli.py', run_name='__main__')\n")
    assert proc.returncode == 2
    assert "required packages are missing" in proc.stderr
    assert "psutil" in proc.stderr
    assert "pip install -r requirements.txt" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_dotenv_loader_parses_example_style_file(tmp_path, monkeypatch):
    import os
    from hood_cli import _load_dotenv
    for key in ("HOOD_T_KEY", "HOOD_T_BLANK", "HOOD_T_QUOTED", "HOOD_T_EXPORTED",
                "HOOD_T_TILDE", "HOOD_T_PRESET", "HOOD_T_HASH"):
        monkeypatch.setenv(key, "x")  # records the original state so teardown restores it
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOOD_T_PRESET", "from-environment")
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "HOOD_T_KEY=abc123   # inline comment\n"
        "HOOD_T_BLANK=            # Fernet key (blank -> not set)\n"
        "HOOD_T_QUOTED=\"a # b\"\n"
        "export HOOD_T_EXPORTED=yes\n"
        "HOOD_T_TILDE=~/.hood\n"
        "HOOD_T_PRESET=from-file\n"
        "HOOD_T_HASH=pa#ss\n"
        "not a setting\n", encoding="utf-8")
    assert _load_dotenv(env) == 5
    assert os.environ["HOOD_T_KEY"] == "abc123"
    assert "HOOD_T_BLANK" not in os.environ
    assert os.environ["HOOD_T_QUOTED"] == "a # b"
    assert os.environ["HOOD_T_EXPORTED"] == "yes"
    assert os.environ["HOOD_T_TILDE"] == os.path.expanduser("~/.hood")
    assert os.environ["HOOD_T_PRESET"] == "from-environment"  # real env wins
    assert os.environ["HOOD_T_HASH"] == "pa#ss"               # '#' inside a value is kept
    assert _load_dotenv(tmp_path / "missing.env") == 0


def test_status_names_missing_pricing_when_key_is_set(tmp_path):
    base = ("import os, sys, json; sys.path.insert(0, os.getcwd())\n"
            "from hood_cli import HoodSystemRuntime\n"
            "print(json.dumps(HoodSystemRuntime().health_check()['providers']))\n")
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("HOOD_MODEL_PRICING", "GEMINI_API_KEY", "GOOGLE_API_KEY", "HOOD_GEMINI_CREDENTIAL")}
    env.update(HOOD_DATA_DIR=str(tmp_path), GEMINI_API_KEY="test-key-not-real")

    def providers(extra):
        proc = subprocess.run([sys.executable, "-c", base], cwd=ROOT, capture_output=True,
                              text=True, timeout=120, env={**env, **extra})
        assert proc.returncode == 0, proc.stderr
        import json
        return json.loads(proc.stdout.strip().splitlines()[-1])

    assert providers({})["gemini"].startswith("KEY_SET_BUT_NO_PRICING")
    priced = providers({"HOOD_MODEL_PRICING": "config/model_pricing.free-tier.json"})
    assert priced["gemini"] == "ONLINE"


def test_dotenv_loader_reads_powershell_utf16_file(tmp_path, monkeypatch):
    import os
    from hood_cli import _load_dotenv
    monkeypatch.setenv("HOOD_T_UTF16", "x")
    monkeypatch.delenv("HOOD_T_UTF16")
    env = tmp_path / ".env"
    env.write_bytes("HOOD_T_UTF16=from-powershell\r\n".encode("utf-16"))  # what `>` writes on PS 5
    assert _load_dotenv(env) == 1
    assert os.environ["HOOD_T_UTF16"] == "from-powershell"


def test_cli_uses_notepad_env_txt_and_reports_provider_state(tmp_path):
    import os
    env_txt = tmp_path / ".env.txt"
    env_txt.write_text(f"HOOD_DATA_DIR={tmp_path / 'data'}\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("HOOD_MODEL_PRICING", "GEMINI_API_KEY", "GOOGLE_API_KEY",
                        "HOOD_GEMINI_CREDENTIAL", "HOOD_DATA_DIR")}
    env["HOOD_ENV_FILE"] = str(tmp_path / ".env")  # .env absent, Notepad's .env.txt present
    proc = subprocess.run([sys.executable, "hood_cli.py", "status"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "rename it to .env" in proc.stderr
    assert "Loaded 1 setting(s)" in proc.stderr
    assert "gemini: CONFIGURED_PENDING_KEY" in proc.stdout


def test_missing_key_error_tells_the_user_what_to_do(monkeypatch):
    import pytest
    from packages.contracts import ModelRequest
    from services.model_gateway.base import ProviderNotConfiguredError
    from services.model_gateway.gemini_adapter import GeminiProviderAdapter
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HOOD_GEMINI_CREDENTIAL"):
        monkeypatch.delenv(k, raising=False)

    class NoVault:
        def get_secret(self, *_a, **_k):
            return None

    adapter = GeminiProviderAdapter(vault=NoVault())
    with pytest.raises(ProviderNotConfiguredError, match="add GEMINI_API_KEY=.* to the .env file"):
        adapter.invoke(ModelRequest(prompt="hi"))
