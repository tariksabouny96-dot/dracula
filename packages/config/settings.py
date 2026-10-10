"""
HOOD System Configuration Models
Governed by Master System Specification Section 19 & Build Instructions Section 8.
"""

from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class EnvironmentProfile(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class BudgetSettings(BaseModel):
    max_task_spend_usd: float = 2.0
    max_daily_spend_usd: float = 10.0
    max_monthly_spend_usd: float = 50.0
    max_task_model_calls: int = 25
    max_task_deep_model_calls: int = 4
    max_parallel_workers: int = 4
    max_task_runtime_sec: int = 300
    hard_stop_on_budget_exceeded: bool = True


class ModelProviderConfig(BaseModel):
    enabled: bool = False
    is_paid: bool = False
    api_key_secret_ref: Optional[str] = None
    default_fast_model: str = "default-fast"
    default_standard_model: str = "default-standard"
    default_deep_model: str = "default-deep"
    timeout_sec: int = 30
    max_retries: int = 3


class StorageSettings(BaseModel):
    backend: str = "sqlite"  # 'sqlite' for dev-temporary, 'postgresql' for production
    sqlite_path: str = "artifacts/hood_data.db"
    postgres_dsn: Optional[str] = None
    enable_pgvector: bool = False
    artifacts_dir: str = "artifacts/data"


class SecuritySettings(BaseModel):
    owner_name: str = "Zak"
    require_explicit_approval_for_money: bool = True
    require_explicit_approval_for_prod: bool = True
    require_explicit_approval_for_destructive: bool = True
    allowed_workspace_roots: List[str] = Field(default_factory=lambda: ["."])
    secret_vault_backend: str = "encrypted_local"  # 'encrypted_local' or 'dpapi'
    secret_vault_file: str = "artifacts/vault.enc"


class SystemConfig(BaseModel):
    environment: EnvironmentProfile = EnvironmentProfile.DEVELOPMENT
    node_role: str = "dev-temporary"
    budgets: BudgetSettings = Field(default_factory=BudgetSettings)
    providers: Dict[str, ModelProviderConfig] = Field(default_factory=dict)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
