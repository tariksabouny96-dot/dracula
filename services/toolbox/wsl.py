"""HOOD's own Linux sandbox on Windows (WSL2), set up and run by HOOD after ONE owner approval.

Owner's rule (2026-10-10): the owner approves or denies; once approved, HOOD does the work. The
only steps Windows itself reserves for the owner are highlighted up front: confirming Windows'
administrator prompt if WSL isn't installed yet, and a restart if Windows asks for one.

What setup does (in order, resumable after a restart):
1. WSL missing -> starts `wsl --install --no-distribution` elevated (Windows shows its admin prompt);
2. downloads Ubuntu 24.04's official WSL image from cloud-images.ubuntu.com through HOOD's firewall
   and checks it against Ubuntu's published SHA-256 before use;
3. imports it as a separate distro named "HOOD" (the owner's own WSL distros are not touched),
   switches off Windows interop inside it (so code there can't start Windows programs) and makes
   root the default user (it is HOOD's own system);
4. installs Python 3 and pytest there.

Every agent run then happens inside that distro, in a fresh user+mount+network namespace:
only the folders the run needs are bound in (the mission folder writable, code read-only),
all Windows drives (/mnt) are hidden, there is no network (loopback only), and CPU, memory, file
size and open files are limited.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, List, Optional, Tuple

DISTRO = "HOOD"
ROOTFS_URL = "https://cloud-images.ubuntu.com/wsl/releases/noble/current/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"
SUMS_URL = "https://cloud-images.ubuntu.com/wsl/releases/noble/current/SHA256SUMS"
ROOTFS_HOST = "cloud-images.ubuntu.com"
BASE_PACKAGES = ("python3", "python3-pytest", "util-linux", "ca-certificates")
MAX_ROOTFS = 900 * 1024 * 1024
WSL_CONF = ("[user]\ndefault=root\n[interop]\nenabled=false\nappendWindowsPath=false\n"
            "[automount]\nenabled=true\n[boot]\nsystemd=false\n")
# Runs inside the distro as root: disable interop, then a fresh user+mount+net namespace that
# binds only what the run needs and hides every Windows drive.
RUN_WRAPPER = r"""
set -e
for f in /proc/sys/fs/binfmt_misc/WSLInterop*; do [ -w "$f" ] && echo 0 > "$f" 2>/dev/null || true; done
exec unshare -rmn --propagation private -- sh -c '
set -e
while IFS="|" read -r src dest mode; do
  [ -n "$src" ] || continue
  mkdir -p "$dest"; mount --bind "$src" "$dest"
  if [ "$mode" = ro ]; then mount -o remount,bind,ro "$dest"; fi
done <<HOODBINDS
$HOOD_BINDS
HOODBINDS
mount -t tmpfs -o size=1m,mode=755 hood-hidden /mnt
cd "$HOOD_CWD"
exec prlimit --cpu=$HOOD_CPU --as=$HOOD_AS --fsize=33554432 --nofile=256 -- timeout -k 5 $HOOD_TIMEOUT "$@"
' hood-run "$@"
"""
APPROVAL_TEXT = [
    "HOOD sets up its own Linux system (WSL2) on this PC and runs the agents' code there: no internet, no access "
    "to your Windows files, limited CPU and memory.",
    "Downloads Ubuntu 24.04's official WSL image (about 340 MB) from cloud-images.ubuntu.com, checked against "
    "Ubuntu's published checksum, and installs Python and pytest inside it. Your own WSL setups aren't touched.",
    "Only if WSL itself isn't installed yet: Windows will show its administrator prompt (click Yes), and may ask "
    "to restart; HOOD then finishes on its own.",
]


def to_wsl_path(path: str) -> str:
    """C:\\Users\\zack\\x -> /mnt/c/Users/zack/x (drive paths only)."""
    p = PureWindowsPath(path)
    if not p.drive or not re.fullmatch(r"[A-Za-z]:", p.drive):
        raise ValueError(f"Not a Windows drive path: {path}")
    rest = "/".join(part for part in p.parts[1:])
    return f"/mnt/{p.drive[0].lower()}/{rest}".rstrip("/")


# Things only the owner can change (HOOD highlights them instead of failing with a code).
_OWNER_ONLY = [
    (re.compile(r"HCS_E_HYPERV_NOT_INSTALLED|0x80370102|virtuali[sz]ation|Virtual Machine Platform", re.I),
     "Virtualization is switched off in this PC's firmware (BIOS/UEFI), so Windows can't run WSL2. HOOD can't "
     "change firmware settings: restart into the BIOS/UEFI setup, switch on Intel VT-x / AMD SVM "
     "(\"Virtualization Technology\"), save, then press \"Try again\" in HOOD."),
    (re.compile(r"0x80070070|not enough (disk )?space|There is not enough space", re.I),
     "The disk is full: HOOD's Linux sandbox needs about 2 GB free on the drive where HOOD keeps its data."),
    (re.compile(r"0x80072ee7|0x80072efd|0x80072ee2|could not resolve host", re.I),
     "Windows couldn't download WSL (no internet, or a proxy blocks it). Check the connection, then "
     "press \"Try again\"."),
]


def explain_wsl_error(output: str) -> Optional[str]:
    for pattern, text in _OWNER_ONLY:
        if pattern.search(output or ""):
            return text
    return None


def _decode(out: bytes) -> str:
    """wsl.exe writes UTF-16LE for its own messages and UTF-8 for Linux programs."""
    if len(out) >= 2 and out[1:2] == b"\x00":
        try:
            return out.decode("utf-16-le", errors="replace")
        except UnicodeDecodeError:
            pass
    return out.decode("utf-8", errors="replace").replace("\x00", "")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WslSandbox:
    def __init__(self, data_dir: Path, *, firewall: Any = None, stop_latch: Any = None,
                 runner: Optional[Callable[[List[str], int], Tuple[int, str]]] = None,
                 fetcher: Optional[Callable[[str, int], bytes]] = None):
        self.root = Path(data_dir) / "wsl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.firewall = firewall
        self.stop_latch = stop_latch
        self._run = runner or self._subprocess_run
        self._fetch = fetcher
        self._lock = threading.RLock()
        self._job: Optional[Dict[str, Any]] = None
        self._ready_cache: Optional[Tuple[float, bool]] = None
        self.listeners: List[Callable[[], None]] = []      # told when setup ends (waiting installs/missions)

    # ------------------------------------------------------------------ state
    def _state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, **fields) -> None:
        with self._lock:
            state = self._state()
            state.update(fields)
            tmp = self.state_path.with_name("state.json.tmp")
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            os.replace(tmp, self.state_path)

    # ------------------------------------------------------------------ wsl.exe
    def wsl_exe(self) -> Optional[str]:
        found = shutil.which("wsl.exe") or shutil.which("wsl")
        if found:
            return found
        system = os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32", "wsl.exe")
        return system if os.path.isfile(system) else None

    def wsl(self, *args: str, timeout: int = 120) -> Tuple[int, str]:
        exe = self.wsl_exe()
        if not exe:
            return 127, "wsl.exe not found"
        return self._run([exe, *args], timeout)

    def in_distro(self, script: str, timeout: int = 600) -> Tuple[int, str]:
        """Run a shell script as root inside HOOD's distro (-e: no default shell re-parsing the script)."""
        return self.wsl("-d", DISTRO, "-u", "root", "-e", "sh", "-c", script, timeout=timeout)

    def wsl_installed(self) -> bool:
        rc, _ = self.wsl("--status", timeout=60)
        return rc == 0

    def _distros(self) -> Optional[List[str]]:
        """Installed WSL distro names, or None when wsl.exe couldn't answer."""
        rc, out = self.wsl("-l", "-q", timeout=60)
        return [line.strip() for line in out.splitlines() if line.strip()] if rc == 0 else None

    def distro_exists(self) -> bool:
        return DISTRO in (self._distros() or [])

    def ready(self, fresh: bool = False) -> bool:
        """HOOD's distro exists and can run isolated Python (cached briefly)."""
        if os.name != "nt" and self._run is self._subprocess_run:
            return False
        now = time.time()
        if not fresh and self._ready_cache and now - self._ready_cache[0] < 60:
            return self._ready_cache[1]
        ok = self._state().get("phase") == "ready" and not (self._job and self._job["state"] == "running")
        distros = self._distros() if ok else None
        if ok and distros is None:
            ok = False                           # WSL didn't answer (e.g. still starting): not ready, no change
        elif ok and DISTRO not in distros:
            # Someone removed HOOD's distro (wsl --unregister HOOD): HOOD asks again before re-creating it.
            self._save(phase=None, approved_by=None, approved_at=None,
                       last_error="HOOD's Linux system was removed from WSL.")
            ok = False
        elif ok:
            rc, out = self.in_distro("unshare -rmn true && python3 -c 'import pytest'", timeout=120)
            if rc != 0:      # broken since: offer "Try again" (the owner's approval still stands)
                self._save(phase="failed", last_error=f"The sandbox check failed: {out.strip()[-300:]}")
            ok = rc == 0
        self._ready_cache = (now, ok)
        return ok

    def status(self) -> Dict[str, Any]:
        ready = self.ready()                  # may update the state (distro removed, check failing)
        state = self._state()
        job = dict(self._job) if self._job else None
        phase = state.get("phase") or "not_set_up"
        if job and job["state"] == "running":
            phase = state.get("phase") or "setting_up"     # the step it's on
        return {"distro": DISTRO, "phase": phase, "ready": phase == "ready" and ready,
                "approved_by": state.get("approved_by"), "approved_at": state.get("approved_at"),
                "last_error": state.get("last_error"), "job": job, "approval_text": APPROVAL_TEXT,
                "wsl_found": bool(self.wsl_exe())}

    def approved(self) -> bool:
        return bool(self._state().get("approved_by"))

    def problem(self) -> Optional[str]:
        """Why missions can't use the sandbox yet (None when ready)."""
        if self.ready():
            return None
        phase = self._state().get("phase")
        if phase == "restart_needed":
            return "Windows needs a restart to finish installing WSL2; HOOD continues on its own afterwards."
        if self._job and self._job["state"] == "running":
            return "HOOD is setting up its Linux sandbox right now."
        return ("This needs HOOD's Linux sandbox (WSL2). Approve \"Set up HOOD's sandbox\" once: HOOD does the "
                "rest (download, install, checks).")

    # ------------------------------------------------------------------ setup (owner-approved)
    def request_setup(self, actor: str) -> Dict[str, Any]:
        with self._lock:
            if self._job and self._job["state"] == "running":
                return dict(self._job)
            self._save(approved_by=actor, approved_at=_now(), last_error=None)
            self._allow_host(actor)
            return self._start(actor)

    def resume_if_approved(self) -> Optional[Dict[str, Any]]:
        """At start-up: finish an approved setup (e.g. after the restart Windows asked for)."""
        state = self._state()
        if not state.get("approved_by") or state.get("phase") in ("ready", None, "failed"):
            return None
        if state.get("phase") == "restart_needed" and not self.wsl_installed():
            return None          # Windows hasn't restarted yet: don't show its administrator prompt again
        return self._start(state["approved_by"])

    def restart_windows(self, actor: str) -> str:
        self._save(restart_requested_by=actor, restart_requested_at=_now())
        rc, out = self._run(["shutdown.exe", "/r", "/t", "60", "/c",
                             "HOOD: restarting to finish setting up its Linux sandbox (WSL2)"], 30)
        if rc != 0:
            raise RuntimeError(f"Windows refused the restart: {out[-200:]}")
        return "Windows restarts in 60 seconds. Save your work; HOOD finishes the setup when it starts again."

    def _start(self, actor: str) -> Dict[str, Any]:
        job = {"id": "sbx_" + uuid.uuid4().hex[:12], "state": "running", "log": [], "started": _now(),
               "finished": None, "error": None, "actor": actor}
        self._save(phase="setting_up", last_error=None)
        self._job = job
        threading.Thread(target=self._work, args=(job,), daemon=True).start()
        return dict(job)

    def wait(self, timeout: float = 3600) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while self._job and self._job["state"] == "running" and time.time() < deadline:
            time.sleep(0.05)
        return dict(self._job) if self._job else {}

    def _log(self, job, line: str) -> None:
        job["log"].append(line)
        job["log"] = job["log"][-200:]

    def _work(self, job: Dict[str, Any]) -> None:
        try:
            self._steps(job)
            job["state"] = "done" if self._state().get("phase") == "ready" else "waiting"
        except Exception as exc:
            job["state"], job["error"] = "failed", f"{type(exc).__name__}: {str(exc)[:500]}"
            self._log(job, "Stopped: " + job["error"])
            self._save(phase="failed", last_error=job["error"])
        finally:
            job["finished"] = _now()
            self._ready_cache = None
            for listener in list(self.listeners):
                try:
                    listener()
                except Exception:  # a listener's failure never breaks the setup
                    pass

    def _check_stop(self) -> None:
        if self.stop_latch is not None:
            self.stop_latch.check()

    def _steps(self, job) -> None:
        self._check_stop()
        if not self.wsl_installed():
            self._log(job, "WSL isn't installed: asking Windows to install it (confirm the administrator prompt)…")
            self._save(phase="installing_wsl")
            rc, out = self._run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 "Start-Process -FilePath wsl.exe -ArgumentList '--install','--no-distribution' "
                                 "-Verb RunAs -Wait"], 3600)
            if rc != 0:
                raise RuntimeError(explain_wsl_error(out) or
                                   "The administrator prompt was declined or failed; nothing was changed. "
                                   f"({out.strip()[-200:]})")
            if not self.wsl_installed():
                self._save(phase="restart_needed")
                self._log(job, "Windows needs a restart to finish installing WSL2. HOOD continues after it.")
                return
        self._check_stop()
        if not self.distro_exists():
            self._save(phase="downloading")
            tar = self._download_rootfs(job)
            self._check_stop()
            self._save(phase="importing")
            target = self.root / DISTRO
            target.mkdir(parents=True, exist_ok=True)
            self._log(job, "Creating HOOD's own Linux system (your other WSL setups are not touched)…")
            rc, out = self.wsl("--import", DISTRO, str(target), str(tar), "--version", "2", timeout=1800)
            if rc != 0:
                raise RuntimeError(explain_wsl_error(out) or f"wsl --import failed: {out.strip()[-300:]}")
            try:
                tar.unlink()
            except OSError:
                pass
        self._check_stop()
        self._save(phase="configuring")
        self._log(job, "Switching off Windows interop inside it (code there can't start Windows programs)…")
        conf = WSL_CONF.replace("\n", "\\n")
        rc, out = self.in_distro(f"printf '{conf}' > /etc/wsl.conf", timeout=120)
        if rc != 0:
            raise RuntimeError(f"Could not configure the distro: {out.strip()[-300:]}")
        self.wsl("--terminate", DISTRO, timeout=120)       # wsl.conf applies on the next start
        self._check_stop()
        self._save(phase="packages")
        self._log(job, "Installing Python and pytest inside it (Ubuntu's signed packages)…")
        rc, out = self.in_distro("export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && "
                                 "apt-get install -y -qq --no-install-recommends " + " ".join(BASE_PACKAGES),
                                 timeout=3600)
        if rc != 0:
            raise RuntimeError(f"Installing Python inside the sandbox failed: {out.strip()[-400:]}")
        rc, out = self.in_distro("unshare -rmn true && python3 -c 'import pytest; print(pytest.__version__)'",
                                 timeout=120)
        if rc != 0:
            raise RuntimeError(f"The sandbox check failed: {out.strip()[-300:]}")
        self._save(phase="ready", ready_at=_now(), last_error=None)
        self._log(job, "HOOD's Linux sandbox is ready.")

    def _download_rootfs(self, job) -> Path:
        from .service import Toolbox     # same verified, firewall-governed downloader
        fetch = self._fetch or Toolbox(self.root.parent, firewall=self.firewall)._http_get
        name = ROOTFS_URL.rsplit("/", 1)[1]
        sums = fetch(SUMS_URL, 64 * 1024).decode("utf-8", errors="replace")
        expected = next((line.split()[0] for line in sums.splitlines()
                         if line.strip().endswith(name) and len(line.split()) == 2), None)
        if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RuntimeError("Ubuntu's checksum list doesn't include the WSL image")
        self._log(job, "Downloading Ubuntu 24.04's official WSL image (about 340 MB)…")
        data = fetch(ROOTFS_URL, MAX_ROOTFS)
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("The Ubuntu image doesn't match Ubuntu's published checksum; not used")
        self._log(job, "Checksum matches Ubuntu's.")
        tar = self.root / name
        tar.write_bytes(data)
        return tar

    def _allow_host(self, actor: str) -> None:
        if self.firewall is not None and not any(r.get("host") == ROOTFS_HOST for r in self.firewall.list_rules()):
            self.firewall.allow(ROOTFS_HOST, [443], note="HOOD's Linux sandbox image (allowed by the owner)",
                                added_by=actor, is_root_owner=True)

    # ------------------------------------------------------------------ isolated runs
    def isolated_argv(self, argv: List[str], cwd: str, writable: List[str], readonly: List[str],
                      launcher: str, timeout: int, cpu: int = 120, memory_bytes: int = 2 * 1024 ** 3) -> List[str]:
        """wsl.exe command running ``argv`` in a fresh namespace inside HOOD's distro.

        Windows paths in argv/cwd under the bound folders are translated; anything else stays."""
        binds, mapping = [], []
        for n, (path, mode) in enumerate([(p, "rw") for p in writable] + [(p, "ro") for p in readonly]):
            dest = f"/run/hood/b{n}"
            binds.append(f"{to_wsl_path(path)}|{dest}|{mode}")
            mapping.append((PureWindowsPath(path), dest))

        def tr(value: str) -> str:
            if not re.match(r"^[A-Za-z]:[\\/]", value or ""):
                return value
            target = PureWindowsPath(value)
            for src, dest in sorted(mapping, key=lambda m: -len(str(m[0]))):
                try:
                    rel = target.relative_to(src)       # case-insensitive, like Windows
                except ValueError:
                    continue
                return dest if str(rel) == "." else f"{dest}/{rel.as_posix()}"
            raise ValueError(f"{value} is outside the folders bound into the sandbox")
        inner = [tr(a) for a in argv]
        env = {"HOOD_BINDS": "\n".join(binds),        # one per line: folder names may contain spaces
               "HOOD_CWD": tr(cwd), "HOOD_TIMEOUT": str(int(timeout)), "HOOD_CPU": str(int(cpu)),
               "HOOD_AS": str(int(memory_bytes))}
        env_args = [f"{k}={v}" for k, v in env.items()]
        return ["-d", DISTRO, "-u", "root", "-e", "env", "-i", "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/tmp", "LANG=C.UTF-8", "PYTHONDONTWRITEBYTECODE=1", "PYTHONNOUSERSITE=1",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1", "TMPDIR=/tmp", f"PYTHONPATH={tr(cwd)}", *env_args,
                "sh", "-c", RUN_WRAPPER, "hood", "python3", "-I", tr(launcher), *inner]

    def run_isolated(self, argv: List[str], cwd: str, writable: List[str], readonly: List[str], launcher: str,
                     timeout: int, cpu: int = 120, memory_bytes: int = 2 * 1024 ** 3) -> Tuple[Optional[int], bytes]:
        """Run inside HOOD's distro (see isolated_argv). Exit code None = it ran out of time."""
        exe = self.wsl_exe()
        if not exe:
            return 127, b"wsl.exe not found"
        cmd = [exe, *self.isolated_argv(argv, cwd, writable, readonly, launcher, timeout, cpu, memory_bytes)]
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout + 60,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            code, out = proc.returncode, (proc.stdout or b"") + (proc.stderr or b"")
        except subprocess.TimeoutExpired as exc:
            self.terminate()
            code, out = None, (exc.stdout or b"") + (exc.stderr or b"")
        return (None if code == 124 else code), out      # 124: coreutils timeout inside the sandbox

    def terminate(self) -> None:
        """Emergency stop / cancel: stop everything running in HOOD's distro."""
        self.wsl("--terminate", DISTRO, timeout=60)

    @staticmethod
    def _subprocess_run(argv: List[str], timeout: int) -> Tuple[int, str]:
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return proc.returncode, _decode(proc.stdout or b"") + _decode(proc.stderr or b"")
        except (OSError, subprocess.SubprocessError) as exc:
            return 127, str(exc)
