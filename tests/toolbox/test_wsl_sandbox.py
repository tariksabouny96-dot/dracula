"""HOOD's own Linux sandbox on Windows: one owner approval, then HOOD does every step itself.

Windows (wsl.exe, PowerShell, shutdown.exe) is faked; the isolation wrapper itself is executed for
real on Linux when user namespaces are available."""
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from services.agents import sandbox as sandbox_mod
from services.agents.sandbox import Workspace, python_cmd
from services.firewall.policy import NetworkFirewall
from services.toolbox import Toolbox
from services.toolbox import wsl as wsl_mod
from services.toolbox.wsl import DISTRO, ROOTFS_HOST, RUN_WRAPPER, WslSandbox, to_wsl_path

WSL_EXE = r"C:\Windows\System32\wsl.exe"
ROOTFS = b"ubuntu rootfs bytes"


class FakeWindows:
    """wsl.exe + PowerShell + shutdown.exe as HOOD sees them."""

    def __init__(self, wsl_installed=False, needs_restart=True, uac="yes", import_error=None):
        self.wsl_installed = wsl_installed
        self.needs_restart = needs_restart
        self.uac = uac
        self.import_error = import_error
        self.restarted_later = False
        self.distros = []
        self.packages = set()
        self.files = {}
        self.calls = []
        self.scripts = []

    def restart(self):
        if self.restarted_later:
            self.wsl_installed = True

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        if argv[0] == "powershell.exe":
            if self.uac != "yes":
                return 1, "Start-Process : This command cannot be run due to the error: The operation was canceled by the user."
            if self.needs_restart:
                self.restarted_later = True
            else:
                self.wsl_installed = True
            return 0, ""
        if argv[0] == "shutdown.exe":
            return 0, ""
        assert argv[0] == WSL_EXE, argv
        args = argv[1:]
        if args == ["--status"]:
            return (0, "Default Version: 2") if self.wsl_installed else (1, "WSL is not installed")
        if not self.wsl_installed:
            return 1, "The Windows Subsystem for Linux is not installed."
        if args == ["-l", "-q"]:
            return 0, "\n".join(self.distros)
        if args[0] == "--import":
            if self.import_error:
                return 1, self.import_error
            assert args[1] == DISTRO and args[-2:] == ["--version", "2"]
            assert Path(args[3]).read_bytes() == ROOTFS
            self.distros.append(DISTRO)
            return 0, "The operation completed successfully."
        if args[0] == "--terminate":
            return 0, ""
        assert args[:6] == ["-d", DISTRO, "-u", "root", "-e", "sh"] and args[6] == "-c", args
        script = args[7]
        self.scripts.append(script)
        if script.startswith("printf"):
            self.files["/etc/wsl.conf"] = script
            return 0, ""
        if "apt-get" in script:
            self.packages.update(script.split("--no-install-recommends ")[-1].split())
            return 0, "Setting up python3-pytest"
        if script.startswith("unshare -rmn true"):
            return (0, "8.0.0") if "python3-pytest" in self.packages else (1, "No module named pytest")
        if script.startswith("command -v "):
            return (0, "/usr/bin/php") if script == "command -v php" and "php-cli" in self.packages else (1, "")
        if script.startswith("'php' '-r'") or script.startswith("php -r"):
            return 0, "8.3.6"
        if script.startswith("'php' '-m'") or script.startswith("php -m"):
            return 0, "[PHP Modules]\nPDO\npdo_sqlite\nSQLite3\nmbstring\nxml\ndom\n"
        raise AssertionError("unexpected script " + script)


def fetcher(sums_ok=True):
    name = wsl_mod.ROOTFS_URL.rsplit("/", 1)[1]
    digest = hashlib.sha256(ROOTFS if sums_ok else b"other").hexdigest()

    def fetch(url, limit):
        if url == wsl_mod.SUMS_URL:
            return f"{'0' * 64} *other.tar.gz\n{digest} {name}\n".encode()
        if url == wsl_mod.ROOTFS_URL:
            return ROOTFS
        raise AssertionError(url)
    return fetch


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(WslSandbox, "wsl_exe", lambda self: WSL_EXE)


def test_one_approval_then_hood_sets_everything_up_even_across_a_restart(tmp_path, windows):
    win = FakeWindows(wsl_installed=False, needs_restart=True)
    fw = NetworkFirewall(tmp_path / "fw")
    sbx = WslSandbox(tmp_path, firewall=fw, runner=win, fetcher=fetcher())
    assert not sbx.ready() and "Approve" in sbx.problem()
    assert sbx.resume_if_approved() is None                 # nothing happens without the owner's OK
    job = sbx.request_setup("zak")
    assert sbx.wait()["state"] == "waiting", sbx.wait()
    assert sbx.status()["phase"] == "restart_needed" and "restart" in sbx.problem()
    assert ROOTFS_HOST in {r["host"] for r in fw.list_rules()}
    assert sum(c[0] == "powershell.exe" for c in win.calls) == 1
    assert "-Verb RunAs" in win.calls[[c[0] for c in win.calls].index("powershell.exe")][-1]
    # HOOD restarted before Windows did: it must not show Windows' admin prompt again.
    assert WslSandbox(tmp_path, runner=win, fetcher=fetcher()).resume_if_approved() is None
    assert sum(c[0] == "powershell.exe" for c in win.calls) == 1
    # The owner presses "Restart now"; Windows restarts; HOOD starts again and finishes alone.
    assert "60 seconds" in sbx.restart_windows("zak")
    assert ["shutdown.exe", "/r", "/t", "60"] == win.calls[-1][:4]
    win.restart()
    after = WslSandbox(tmp_path, runner=win, fetcher=fetcher())
    assert after.resume_if_approved()["state"] == "running" and job["id"] != after.wait()["id"]
    assert after.wait()["state"] == "done", after.wait()
    assert after.ready(fresh=True) and after.problem() is None and after.status()["phase"] == "ready"
    assert win.distros == [DISTRO]
    conf = win.files["/etc/wsl.conf"]
    assert "[interop]\\nenabled=false" in conf and "appendWindowsPath=false" in conf
    assert {"python3", "python3-pytest", "util-linux"} <= win.packages
    assert not list((tmp_path / "wsl").glob("*.tar.gz"))      # the image is deleted after import
    assert after.approved() and after.status()["approved_by"] == "zak"
    # Removed by hand later (wsl --unregister HOOD): HOOD asks again instead of silently re-creating it.
    win.distros.clear()
    assert not after.ready(fresh=True) and not after.approved() and after.status()["phase"] == "not_set_up"
    assert after.resume_if_approved() is None


def test_wsl_already_installed_means_no_admin_prompt_and_no_restart(tmp_path, windows):
    win = FakeWindows(wsl_installed=True)
    sbx = WslSandbox(tmp_path, runner=win, fetcher=fetcher())
    sbx.request_setup("zak")
    assert sbx.wait()["state"] == "done"
    assert not any(c[0] in ("powershell.exe", "shutdown.exe") for c in win.calls)


def test_image_that_does_not_match_ubuntus_checksum_is_never_imported(tmp_path, windows):
    win = FakeWindows(wsl_installed=True)
    sbx = WslSandbox(tmp_path, runner=win, fetcher=fetcher(sums_ok=False))
    sbx.request_setup("zak")
    job = sbx.wait()
    assert job["state"] == "failed" and "checksum" in job["error"]
    assert win.distros == [] and not any(c[1:2] == ["--import"] for c in win.calls)
    assert sbx.status()["phase"] == "failed" and sbx.resume_if_approved() is None


def test_declined_admin_prompt_changes_nothing(tmp_path, windows):
    win = FakeWindows(wsl_installed=False, uac="no")
    sbx = WslSandbox(tmp_path, runner=win, fetcher=fetcher())
    sbx.request_setup("zak")
    job = sbx.wait()
    assert job["state"] == "failed" and "declined" in job["error"] and win.distros == []


def test_what_only_the_owner_can_fix_is_highlighted_in_plain_words(tmp_path, windows):
    win = FakeWindows(wsl_installed=True, import_error="Error code: Wsl/Service/RegisterDistro/CreateVm/"
                                                       "HCS/HCS_E_HYPERV_NOT_INSTALLED")
    sbx = WslSandbox(tmp_path, runner=win, fetcher=fetcher())
    sbx.request_setup("zak")
    job = sbx.wait()
    assert job["state"] == "failed" and "BIOS/UEFI" in job["error"] and "Try again" in job["error"]


def test_tools_approved_with_the_sandbox_install_inside_it_without_asking_again(tmp_path, windows, monkeypatch):
    monkeypatch.setattr(Toolbox, "on_windows", lambda self: True)
    win = FakeWindows(wsl_installed=False, needs_restart=True)
    sbx = WslSandbox(tmp_path, runner=win, fetcher=fetcher())
    tb = Toolbox(tmp_path, wsl=sbx, runner=win)
    assert "Approve" in tb.needs(["php"])["problem"]
    sbx.request_setup("zak")
    job = tb.request_install(["php"], "zak")              # same click: sandbox + PHP
    assert tb.run_job_now(job["id"])["state"] == "waiting"
    assert "restart" in " ".join(tb.job(job["id"])["log"])
    win.restart()                                          # Windows restarted, HOOD starts again
    sbx2 = WslSandbox(tmp_path, runner=win, fetcher=fetcher())
    tb2 = Toolbox(tmp_path, wsl=sbx2, runner=win)
    sbx2.resume_if_approved()
    resumed = tb2.resume_pending()
    assert tb2.run_job_now(resumed["id"])["state"] == "done", tb2.job(resumed["id"])
    assert "php-cli" in win.packages and tb2.detect("php")["installed"]
    assert next(t for t in tb2.status()["tools"] if t["id"] == "php")["approved_by"] == "zak"
    assert tb2.resume_pending() is None


def test_paths_are_translated_and_nothing_outside_the_bound_folders_is_reachable():
    sbx = WslSandbox.__new__(WslSandbox)
    argv = sbx.isolated_argv(["/usr/bin/python3", "-m", "pytest", r"C:\HOOD data\ws\m1\tests"],
                             r"C:\HOOD data\ws\m1", [r"C:\HOOD data\ws\m1"], [r"C:\hood\services\agents"],
                             r"C:\hood\services\agents\netns_launcher.py", 90)
    assert argv[:6] == ["-d", DISTRO, "-u", "root", "-e", "env"] and argv[6] == "-i"
    env = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in argv if "=" in a and not a.startswith(("-", "/"))
           and "\n" not in a.split("=", 1)[0]}
    assert env["HOOD_BINDS"] == "/mnt/c/HOOD data/ws/m1|/run/hood/b0|rw\n/mnt/c/hood/services/agents|/run/hood/b1|ro"
    assert env["HOOD_CWD"] == "/run/hood/b0" and env["HOOD_TIMEOUT"] == "90" and env["HOOD_CPU"] == "120"
    tail = argv[argv.index(RUN_WRAPPER) + 1:]
    assert tail == ["hood", "python3", "-I", "/run/hood/b1/netns_launcher.py", "/usr/bin/python3", "-m", "pytest",
                    "/run/hood/b0/tests"]
    assert to_wsl_path(r"D:\x\y") == "/mnt/d/x/y"
    with pytest.raises(ValueError):
        sbx.isolated_argv(["python3", r"C:\Users\zak\secret.txt"], r"C:\HOOD data\ws\m1",
                          [r"C:\HOOD data\ws\m1"], [], r"C:\HOOD data\ws\m1\l.py", 10)


class FakeSandbox:
    def __init__(self):
        self.runs = []
        self.terminated = 0

    def ready(self, fresh=False):
        return True

    def problem(self):
        return None

    def run_isolated(self, argv, cwd, writable, readonly, launcher, timeout, cpu=120, memory_bytes=2 * 1024 ** 3):
        self.runs.append((argv, cwd, writable, readonly, launcher, timeout))
        return 0, b"1 passed"

    def terminate(self):
        self.terminated += 1


def test_agent_checks_on_windows_run_inside_hoods_sandbox(tmp_path, monkeypatch):
    fake = FakeSandbox()
    monkeypatch.setattr(sandbox_mod, "_on_windows", lambda: True)
    monkeypatch.setattr(sandbox_mod, "_WINDOWS_SANDBOX", fake)
    assert sandbox_mod.sandbox_problem() is None and sandbox_mod.windows_sandbox_ready()
    ws = Workspace(tmp_path / "ws")
    result = ws.run("unit_tests", python_cmd("-m", "pytest", "-q", "tests"), timeout=90,
                    extra_readonly=[str(tmp_path / "tools")])
    assert result.passed and result.exit_code == 0 and "1 passed" in result.output_tail
    argv, cwd, writable, readonly, launcher, timeout = fake.runs[0]
    assert argv[0] == "/usr/bin/python3" and argv[1:] == ["-B", "-m", "pytest", "-q", "tests"]
    assert cwd == str(ws.root) and writable == [str(ws.root)] and timeout == 90
    assert readonly == [str(Path(launcher).parent), str(tmp_path / "tools")]
    assert ws.network_isolated
    # Owner-approved "Run on my PC" still runs directly, never through the sandbox.
    monkeypatch.setattr(sandbox_mod, "_WINDOWS_SANDBOX", None)
    assert "WSL2" in sandbox_mod.sandbox_problem()


def _userns_mounts_work():
    if not sys.platform.startswith("linux") or not all(shutil.which(b) for b in ("unshare", "prlimit", "timeout")):
        return False
    try:
        return subprocess.run(["unshare", "-rmn", "sh", "-c", "mount -t tmpfs t /mnt"], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@pytest.mark.skipif(not _userns_mounts_work(), reason="needs Linux user+mount namespaces")
def test_isolation_wrapper_really_hides_files_and_network(tmp_path):
    """The exact script HOOD runs inside its WSL distro, executed here on Linux."""
    work, code = tmp_path / "work dir", tmp_path / "code"
    for d in (work, code):
        d.mkdir()
    launcher = Path(sandbox_mod.__file__).with_name("netns_launcher.py")
    shutil.copy(launcher, code / "netns_launcher.py")
    probe = (
        "import os, socket, pathlib\n"
        "open('out.txt', 'w').write('written')\n"
        "print('cwd', os.getcwd())\n"
        "print('mnt', sorted(os.listdir('/mnt')))\n"
        "try:\n    open('/run/hood/b1/x', 'w'); print('code_writable True')\n"
        "except OSError:\n    print('code_writable False')\n"
        "s = socket.socket(); s.settimeout(2)\n"
        "try:\n    s.connect(('1.1.1.1', 443)); print('net True')\n"
        "except OSError:\n    print('net False')\n"
        "l = socket.socket(); l.bind(('127.0.0.1', 0)); print('loopback True')\n")
    (work / "probe.py").write_text(probe)
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOOD_BINDS": f"{work}|/run/hood/b0|rw\n{code}|/run/hood/b1|ro",
           "HOOD_CWD": "/run/hood/b0", "HOOD_TIMEOUT": "30", "HOOD_CPU": "30", "HOOD_AS": str(2 * 1024 ** 3)}
    proc = subprocess.run(["sh", "-c", RUN_WRAPPER, "hood", sys.executable, "-I", "/run/hood/b1/netns_launcher.py",
                           sys.executable, "probe.py"], env=env, capture_output=True, text=True, timeout=60)
    out = proc.stdout
    assert proc.returncode == 0, proc.stderr
    assert "cwd /run/hood/b0" in out and (work / "out.txt").read_text() == "written"
    assert "mnt []" in out and "code_writable False" in out and "net False" in out and "loopback True" in out
