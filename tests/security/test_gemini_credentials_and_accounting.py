"""Security batch 1/S3: Gemini credentials, model configuration and accurate spend accounting.

Before the batch: the container never received a usable key (only HOOD_GEMINI_CREDENTIAL=proxy, which
needs a key-injecting proxy), embeddings ignored the key saved in Settings, the adapter ignored the
models and key reference in hood.config.yaml, calls were billed at the dearest candidate's price
instead of the model that answered, voice left out "thinking" tokens and priced audio at the text
rate, embeddings were never charged at all, and a restart reset the daily/monthly caps to zero.
"""
import json
from pathlib import Path

import pytest

from packages.auth.vault import SecretVault
from packages.config import SystemConfig
from packages.config.loader import load_config
from packages.config.pricing import ModelPrice, load_price_table
from packages.contracts import ModelClass, ModelRequest, ModelResponse, ModelUsage, ProviderName
from services.learning.embeddings import GeminiEmbeddingProvider
from services.model_gateway.base import BaseModelProvider
from services.model_gateway.cost_controller import BudgetExceededError, CostController
from services.model_gateway.gemini_adapter import GeminiProviderAdapter
from services.model_gateway.gemini_usage import usage_from_metadata
from services.model_gateway.router import ModelRouter

ROOT = Path(__file__).resolve().parents[2]
KEY = "AIza" + "x" * 35


def price(i, o, audio=None):
    return ModelPrice(i, o, "2026-10-09", "test", audio)


class Response:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self, limit):
        return self.data[:limit]


# ------------------------------------------------------------------ credentials
def test_the_key_saved_in_settings_is_the_runtime_vaults_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    vault = SecretVault(tmp_path / "custom" / "vault.enc")          # e.g. hood.config.yaml points elsewhere
    router = ModelRouter(SystemConfig(), vault=vault, price_table={})
    gemini = router.providers[ProviderName.GEMINI]
    assert gemini.vault is vault and gemini._get_api_key() is None
    vault.set_secret("gemini", "api_key", "  " + KEY + "\n")           # pasted with spaces
    assert gemini._get_api_key() == KEY


def test_environment_key_is_cleaned_and_vault_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", f' "{KEY}" \n')
    adapter = GeminiProviderAdapter(vault=SecretVault(tmp_path / "v.enc"))
    assert adapter._get_api_key() == KEY
    adapter.vault.set_secret("gemini", "api_key", "AIza" + "y" * 35)
    assert adapter._get_api_key() == "AIza" + "y" * 35


def test_proxy_credential_needs_a_real_proxy(monkeypatch):
    monkeypatch.setenv("HOOD_GEMINI_CREDENTIAL", "proxy")
    for var in ("HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    assert GeminiProviderAdapter._proxy_credential() is False      # a server would send no key at all
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    assert GeminiProviderAdapter._proxy_credential() is True


def test_the_container_receives_a_real_key_setting():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert 'GEMINI_API_KEY: "${GEMINI_API_KEY:-}"' in compose
    assert "HOOD_GEMINI_CREDENTIAL:" not in compose


# ------------------------------------------------------------------ model configuration
def test_models_come_from_env_then_config_then_defaults(tmp_path, monkeypatch):
    for cls in ("FAST", "STANDARD", "DEEP"):
        monkeypatch.delenv(f"HOOD_GEMINI_{cls}_MODEL", raising=False)
    cfg_file = tmp_path / "hood.config.yaml"
    cfg_file.write_text("providers:\n  gemini:\n    enabled: true\n    default_fast_model: gemini-cfg-fast\n"
                        "    default_standard_model: gemini-cfg-standard\n    timeout_sec: 240\n"
                        "    api_key_secret_ref: SECRET://gemini/team_key\n")
    router = ModelRouter(load_config(cfg_file), price_table={}, vault=SecretVault(tmp_path / "v.enc"))
    g = router.providers[ProviderName.GEMINI]
    assert g.resolve_model(ModelRequest(prompt="x", model_class=ModelClass.FAST)) == "gemini-cfg-fast"
    assert g.resolve_model(ModelRequest(prompt="x", model_class=ModelClass.STANDARD)) == "gemini-cfg-standard"
    assert g.timeout_sec == 240 and g.api_key_secret_ref == "SECRET://gemini/team_key"
    monkeypatch.setenv("HOOD_GEMINI_FAST_MODEL", "gemini-env-fast")
    assert g.resolve_model(ModelRequest(prompt="x", model_class=ModelClass.FAST)) == "gemini-env-fast"
    # An incomplete config (placeholder "default-*" names) falls back to the built-in models.
    placeholder = GeminiProviderAdapter(vault=SecretVault(tmp_path / "w.enc"),
                                        models={ModelClass.DEEP: "default-deep"})
    assert placeholder.resolve_model(ModelRequest(prompt="x", model_class=ModelClass.DEEP)) == "gemini-3.8-flash"


# ------------------------------------------------------------------ accounting
class TwoModelGemini(BaseModelProvider):
    """Primary is dear, the fallback that actually answers is cheap."""

    def __init__(self):
        super().__init__(ProviderName.GEMINI, enabled=True)

    def is_healthy(self):
        return True

    def resolve_model(self, request):
        return "gemini-dear"

    def candidate_models(self, request):
        return ["gemini-dear", "gemini-cheap"]

    def invoke(self, request):
        return ModelResponse(text="ok", provider=ProviderName.GEMINI, model_name="gemini-cheap-002",
                             usage=ModelUsage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000),
                             latency_ms=1, is_fallback=True, requested_model="gemini-cheap")


def test_the_model_that_answered_is_billed_not_the_dearest_candidate():
    table = {"gemini": {"gemini-dear": price(0.01, 0.04), "gemini-cheap": price(0.001, 0.002)}}
    router = ModelRouter(SystemConfig(), price_table=table)
    router.providers = {ProviderName.GEMINI: TwoModelGemini()}
    resp = router.invoke(ModelRequest(prompt="hello", task_id="t1", max_tokens=1000))
    assert resp.usage.estimated_cost_usd == pytest.approx(0.003)          # cheap: 1k in + 1k out
    call = router.cost_controller.get_call_history("t1")[0]
    assert call["model"] == "gemini-cheap" and call["cost_usd"] == pytest.approx(0.003)


def test_thinking_tokens_are_billed_as_output_everywhere(monkeypatch):
    meta = {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 300,
            "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 40}, {"modality": "AUDIO", "tokenCount": 60}]}
    usage, audio = usage_from_metadata(meta)
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, audio) == (100, 320, 420, 60)

    class Vault:
        def get_secret(self, ref):
            return KEY
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: Response(
        {"candidates": [{"content": {"parts": [{"text": "hi"}]}}], "usageMetadata": meta,
         "modelVersion": "gemini-3.8-flash-001"}))
    resp = GeminiProviderAdapter(vault=Vault()).invoke(ModelRequest(prompt="hi"))
    assert resp.usage.completion_tokens == 320 and resp.requested_model == "gemini-3.8-flash"
    assert resp.model_name == "gemini-3.8-flash-001" and resp.audio_prompt_tokens == 60


def test_audio_input_is_priced_at_its_own_rate():
    p = price(0.0003, 0.0025, audio=0.001)
    assert p.cost(1000, 0, audio_prompt_tokens=1000) == pytest.approx(0.001)
    assert p.cost(1000, 0, audio_prompt_tokens=0) == pytest.approx(0.0003)
    assert price(0.0003, 0.0025).cost(1000, 0, 1000) == pytest.approx(0.0003)   # no audio price: text rate


def test_spend_caps_survive_a_restart(tmp_path):
    from packages.config import BudgetSettings
    budgets = BudgetSettings(max_daily_spend_usd=1.0, max_monthly_spend_usd=5.0, max_task_spend_usd=5.0)
    ledger = tmp_path / "spend_ledger.sqlite3"
    first = CostController(budgets, ledger_path=ledger)
    rid = first.reserve("t", 0.9)
    first.settle(rid, ModelUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20, estimated_cost_usd=0.9),
                 cost_measured=True, provider="gemini", model="gemini-3.8-flash")
    restarted = CostController(budgets, ledger_path=ledger)              # HOOD restarted
    assert restarted.daily_spend_usd == pytest.approx(0.9) and restarted.monthly_spend_usd == pytest.approx(0.9)
    with pytest.raises(BudgetExceededError, match="Daily"):
        restarted.reserve("t2", 0.2)
    assert len(restarted.call_history) == 0 and restarted.total_tokens_consumed == 20


def test_embeddings_use_the_settings_key_need_a_price_and_are_charged(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    vault = SecretVault(tmp_path / "v.enc")
    vault.set_secret("gemini", "api_key", KEY)
    router = ModelRouter(SystemConfig(), vault=vault, price_table={})
    g = router.providers[ProviderName.GEMINI]
    emb = GeminiEmbeddingProvider(key_source=g._get_api_key, proxy_source=g._proxy_credential, router=router)
    assert emb.available()                                               # was False: env only
    with pytest.raises(RuntimeError, match="No price on file"):
        emb.embed(["hello"])
    router.price_table = {"gemini": {"gemini-embedding-001": price(0.00015, 0.0)}}
    seen = []

    def urlopen(req, timeout):
        seen.append(req.get_header("X-goog-api-key"))
        return Response({"embedding": {"values": [0.1, 0.2]}})
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert emb.embed(["hello world"]) == [[0.1, 0.2]]
    assert seen == [KEY]
    call = router.cost_controller.get_call_history()[0]
    assert call["model"] == "gemini-embedding-001" and call["cost_usd"] > 0 and call["cost_measured"] is False


def test_price_files_are_honest():
    free = json.loads((ROOT / "config" / "model_pricing.free-tier.json").read_text())
    assert "gemini-3.1-pro-preview" not in free["gemini"]               # no free tier exists for it
    paid = load_price_table(str(ROOT / "config" / "model_pricing.paid.example.json"))
    assert paid["gemini"]["gemini-3.8-flash"].output_per_1k_usd == pytest.approx(0.00375)
    assert all(p.input_per_1k_usd > 0 for p in paid["gemini"].values())


def test_the_runtime_keeps_a_durable_spend_ledger_in_the_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOD_DATA_DIR", str(tmp_path / "data"))
    import hood_cli
    rt = hood_cli.HoodSystemRuntime()
    assert rt.cost_controller.ledger_path == tmp_path / "data" / "spend_ledger.sqlite3"
    assert rt.model_router.providers[ProviderName.GEMINI].vault is rt.vault
