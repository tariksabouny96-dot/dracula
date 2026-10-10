"""HOOD egress firewall: default-deny, owner authority, fail-closed, stop-aware."""
import pytest

from services.firewall.policy import NetworkFirewall, FirewallBlocked
from packages.security import StopLatch


def test_default_deny_until_owner_allows(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    assert fw.authorize("generativelanguage.googleapis.com").allowed is False
    # An agent (not root owner) cannot widen policy.
    with pytest.raises(PermissionError):
        fw.allow("generativelanguage.googleapis.com", is_root_owner=False)
    # The owner can.
    fw.allow("generativelanguage.googleapis.com", [443], note="gemini", is_root_owner=True)
    assert fw.authorize("generativelanguage.googleapis.com", 443).allowed is True
    # Wrong port is still denied.
    assert fw.authorize("generativelanguage.googleapis.com", 8080).allowed is False


def test_wildcard_matches_subdomains_only(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    fw.allow("*.googleapis.com", [443], is_root_owner=True)
    assert fw.authorize("generativelanguage.googleapis.com").allowed is True
    assert fw.authorize("googleapis.com").allowed is False            # bare apex not matched
    assert fw.authorize("googleapis.com.evil.test").allowed is False  # suffix spoof not matched


def test_loopback_allowed_and_emergency_stop_blocks_everything(tmp_path):
    latch = StopLatch()
    fw = NetworkFirewall(tmp_path / "fw", stop_latch=latch)
    fw.allow("api.example.com", [443], is_root_owner=True)
    assert fw.authorize("127.0.0.1", 8990).allowed is True
    assert fw.authorize("api.example.com").allowed is True
    latch.engage("test")
    assert fw.authorize("api.example.com").allowed is False
    assert fw.authorize("127.0.0.1", 8990).allowed is False  # stop halts all egress


def test_enforce_raises_and_persists_across_instances(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    with pytest.raises(FirewallBlocked):
        fw.enforce("api.example.com")
    rule = fw.allow("api.example.com", [443], is_root_owner=True)
    fw.enforce("api.example.com")  # no raise now
    # A fresh instance reloads the persisted ruleset.
    fw2 = NetworkFirewall(tmp_path / "fw")
    assert any(r["id"] == rule["id"] for r in fw2.list_rules())
    assert fw2.authorize("api.example.com").allowed is True


def test_revoke_requires_owner_and_unreadable_policy_fails_closed(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    rule = fw.allow("api.example.com", [443], is_root_owner=True)
    with pytest.raises(PermissionError):
        fw.revoke(rule["id"], is_root_owner=False)
    assert fw.revoke(rule["id"], is_root_owner=True) is True
    assert fw.authorize("api.example.com").allowed is False
    # A corrupt rules file must not grant anything.
    (tmp_path / "fw" / "rules.json").write_text("{ not json", encoding="utf-8")
    fw3 = NetworkFirewall(tmp_path / "fw")
    assert fw3.authorize("api.example.com").allowed is False


def test_seed_defaults_is_additive(tmp_path):
    fw = NetworkFirewall(tmp_path / "fw")
    fw.seed_defaults(["generativelanguage.googleapis.com"])
    assert fw.authorize("generativelanguage.googleapis.com").allowed is True
    # Seeding again does not duplicate.
    fw.seed_defaults(["generativelanguage.googleapis.com"])
    assert sum(1 for r in fw.list_rules() if r["host"] == "generativelanguage.googleapis.com") == 1
