import pytest
from pathlib import Path
from packages.config import load_config, get_default_config, EnvironmentProfile, SystemConfig

def test_default_config_structure():
    cfg = get_default_config()
    assert cfg.environment == EnvironmentProfile.DEVELOPMENT
    assert cfg.node_role == "dev-temporary"
    assert cfg.budgets.max_daily_spend_usd == 10.0
    assert cfg.security.owner_name == "Zak"
    # OpenAI must be disabled by default
    assert cfg.providers["openai"].enabled is False
    # Mock must be enabled
    assert cfg.providers["mock"].enabled is True

def test_load_config_fallback(tmp_path):
    missing_file = tmp_path / "non_existent.yaml"
    cfg = load_config(missing_file)
    assert isinstance(cfg, SystemConfig)
    assert cfg.security.owner_name == "Zak"
