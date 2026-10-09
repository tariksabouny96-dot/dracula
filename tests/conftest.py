"""Suite profiles.

Tests that need a real paid provider, the public internet or Windows hardware
are *skipped with an explicit reason* unless their opt-in variable is set.
A skip is never a pass: scripts/preproduction_gate.py counts every skipped
live test as missing evidence (BLOCKED), so it cannot turn a release GO.
"""
import os
import sys

import pytest

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


@pytest.fixture(autouse=True)
def _isolated_hood_data_dir(tmp_path_factory, monkeypatch):
    """No test may write into the real ~/.hood: every test gets its own data dir."""
    if "HOOD_DATA_DIR" not in os.environ or os.environ.get("HOOD_TEST_KEEP_DATA_DIR") != "1":
        monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path_factory.mktemp("hood-data")))
