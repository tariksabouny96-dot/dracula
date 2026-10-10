"""Suite profiles.

Tests that need a real paid provider, the public internet or Windows hardware
are *skipped with an explicit reason* unless their opt-in variable is set.
A skip is never a pass: scripts/preproduction_gate.py counts every skipped
live test as missing evidence (BLOCKED), so it cannot turn a release GO.
"""
import os
import socket
import sys

import pytest

# On the Windows CI runner, pytest-timeout can only use the `thread` method,
# which cannot interrupt a thread blocked inside a native socket call. Several
# tests talk to a loopback HTTP server with urllib, which has no default
# timeout, so a single stalled connection would hang the whole job until the
# runner ceiling (observed on windows-2022). Bounding every socket operation
# turns any such stall into a prompt, named failure instead of a 30-minute
# hang. Loopback operations complete in milliseconds, so this never trips in
# normal runs. Left untouched on Linux/macOS, where the signal-based timeout
# works and this is the gating platform.
if sys.platform == "win32" and os.environ.get("HOOD_NO_SOCKET_TIMEOUT") != "1":
    socket.setdefaulttimeout(60)

PROFILES = {
    "live_provider": ("HOOD_RUN_LIVE_PROVIDER", "live provider test: set HOOD_RUN_LIVE_PROVIDER=1 with an approved key, "
                                                "pricing file and spend cap"),
    "live_network": ("HOOD_RUN_LIVE_NETWORK", "live internet test: set HOOD_RUN_LIVE_NETWORK=1 on a host with outbound access"),
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        for marker, (env, reason) in PROFILES.items():
            if item.get_closest_marker(marker) and os.environ.get(env) != "1":
                item.add_marker(pytest.mark.skip(reason="BLOCKED_EXTERNAL: " + reason))
        if item.get_closest_marker("windows_only") and sys.platform != "win32":
            item.add_marker(pytest.mark.skip(reason="BLOCKED_TARGET: requires the owner's Windows machine"))
        # Real-browser E2E drives a headless Chromium against the web console,
        # which is a Linux-served production surface fully exercised by the
        # Linux CI job. Headless Chromium on the Windows runner is prone to
        # hang/flake, so these are skipped there with an explicit reason
        # (never a silent pass).
        if item.get_closest_marker("browser_e2e") and sys.platform == "win32":
            item.add_marker(pytest.mark.skip(
                reason="BLOCKED_TARGET: real-browser E2E runs on Linux CI; the web console is a Linux-served surface"))


@pytest.fixture(autouse=True)
def _isolated_hood_data_dir(tmp_path_factory, monkeypatch):
    """No test may write into the real ~/.hood: every test gets its own data dir."""
    if "HOOD_DATA_DIR" not in os.environ or os.environ.get("HOOD_TEST_KEEP_DATA_DIR") != "1":
        monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path_factory.mktemp("hood-data")))
    # ...and no test copies the machine's real pre-batch-1 stores (artifacts/) into its data dir.
    monkeypatch.setenv("HOOD_SKIP_LEGACY_MIGRATION", "1")
