"""The sandbox's loopback helper on kernels that refuse the classic ioctl (gVisor answers ENOTTY,
seen on the platform-validation runners): netlink brings lo up instead, and if nothing can, the
command still runs without 127.0.0.1. The empty network namespace (the isolation) never changes."""
import shutil
import subprocess
import sys
from pathlib import Path

from tests.agents.test_agent_engine import needs_netns

LAUNCHER = Path(__file__).resolve().parents[2] / "services" / "agents" / "netns_launcher.py"

PRELUDE = f"""
import errno, fcntl, importlib.util, socket, sys
spec = importlib.util.spec_from_file_location("launcher", {str(LAUNCHER)!r})
launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
real_ioctl = fcntl.ioctl
def gvisor_ioctl(fd, request, arg=0, *rest):
    if request == launcher.SIOCSIFFLAGS:
        raise OSError(errno.ENOTTY, "Inappropriate ioctl for device")
    return real_ioctl(fd, request, arg, *rest)
launcher.fcntl.ioctl = gvisor_ioctl
"""

PROBE = """
l = socket.socket(); l.bind(("127.0.0.1", 0)); l.listen(1)
c = socket.create_connection(l.getsockname(), timeout=2); print("loopback ok")
try:
    socket.create_connection(("1.1.1.1", 80), timeout=2); print("EXTERNAL REACHABLE")
except OSError:
    print("external blocked")
"""


def _in_netns(code):
    return subprocess.run([shutil.which("unshare"), "-rn", sys.executable, "-I", "-c", code],
                          capture_output=True, text=True, timeout=60)


@needs_netns
def test_netlink_brings_loopback_up_when_the_ioctl_is_refused():
    out = _in_netns(PRELUDE + "assert launcher.loopback_up()\n" + PROBE)
    assert out.returncode == 0, out.stderr
    assert "loopback ok" in out.stdout and "external blocked" in out.stdout


@needs_netns
def test_without_any_way_to_raise_loopback_the_command_still_runs_isolated():
    code = PRELUDE + ("launcher.socket.if_nametoindex = lambda name: (_ for _ in ()).throw(OSError('no netlink'))\n"
                      "assert not launcher.loopback_up()\n"
                      f"sys.argv = ['launcher', sys.executable, '-I', '-c', {PROBE!r}]\n"
                      "launcher.main()\n")
    out = _in_netns(code)
    assert "127.0.0.1 is unavailable" in out.stderr and "still no network" in out.stderr
    assert "EXTERNAL REACHABLE" not in out.stdout and "loopback ok" not in out.stdout
