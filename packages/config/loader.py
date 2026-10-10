"""
HOOD Configuration Loader
Safely loads system configuration without exposing secrets.
"""

from pathlib import Path
from typing import Optional
import yaml
from .settings import SystemConfig, EnvironmentProfile, ModelProviderConfig, BudgetSettings, StorageSettings, SecuritySettings

DEFAULT_CONFIG_PATH = Path("hood.config.yaml")
EXAMPLE_CONFIG_PATH = Path("hood.config.example.yaml")


def get_default_config() -> SystemConfig:
    return SystemConfig(
        environment=EnvironmentProfile.DEVELOPMENT,
        node_role="dev-temporary",
        budgets=BudgetSettings(),
        providers={
            "gemini": ModelProviderConfig(
                enabled=True,
                is_paid=False,
                api_key_secret_ref="SECRET://gemini/api_key",
                default_fast_model="gemini-3.5-flash-lite",
                default_standard_model="gemini-3.8-flash",
                default_deep_model="gemini-3.8-flash",
            ),
            "openai": ModelProviderConfig(
                enabled=False,  # Disabled by default as OpenAI is not authorized/configured
                is_paid=True,
                api_key_secret_ref="SECRET://openai/api_key",
                default_fast_model="gpt-4o-mini",
                default_standard_model="gpt-4o",
                default_deep_model="o3-mini",
            ),
            "mock": ModelProviderConfig(
                enabled=True,
                is_paid=False,
                api_key_secret_ref=None,
                default_fast_model="mock-fast",
                default_standard_model="mock-standard",
                default_deep_model="mock-deep",
            )
        },
        storage=StorageSettings(),
        security=SecuritySettings()
    )


def load_config(config_path: Optional[Path] = None) -> SystemConfig:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.is_file():
        # Fallback to default
        cfg = get_default_config()
        return cfg

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return SystemConfig(**data)


def save_config(config: SystemConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(mode="json"), f, indent=2, sort_keys=False)
