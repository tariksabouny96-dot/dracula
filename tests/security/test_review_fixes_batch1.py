"""Fixes for the independent review of security batch 1 (each test reproduces a finding)."""
import threading
from pathlib import Path

import pytest

from packages.config import BudgetSettings, SystemConfig
from packages.config.pricing import ModelPrice
from packages.contracts import ModelRequest, ModelResponse, ModelUsage, ProviderName
from packages.security.client import rate_bucket
from services.auth.auth_service import AuthenticationService
from services.model_gateway.base import BaseModelProvider, ProviderRateLimitError
from services.model_gateway.cost_controller import CostController
from services.model_gateway.router import ModelRouter
from tests.security.test_proxy_owner_boundaries import PASSWORD, call, server, via_proxy  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


def _owner(tmp_path):
    auth = AuthenticationService(db_path=tmp_path / "auth.db")
    auth.initialize_root_owner("zak", "Zak", PASSWORD)
    return auth


def test_parallel_wrong_passwords_cannot_exceed_the_limit(tmp_path):
    """Finding 1: check and count were separate, so 60 parallel guesses were all evaluated."""
    auth = _owner(tmp_path)
    results, barrier = [], threading.Barrier(30)

    def guess():
        barrier.wait()
        try:
            results.append(auth.authenticate("zak", "wrong-password-1", ip_address="203.0.113.66"))
        except ValueError:
            results.append("limited")
    threads = [threading.Thread(target=guess) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("limited") >= 25                 # at most 5 guesses were checked
    assert len(auth.rate_limiter.attempts) <= 2           # no unbounded growth of empty keys


def test_ipv6_addresses_in_one_slash64_share_a_bucket():
    assert rate_bucket("2001:db8:1:2::1") == rate_bucket("2001:db8:1:2:ffff:ffff:ffff:ffff") == "2001:db8:1:2::/64"
    assert rate_bucket("2001:db8:1:3::1") != rate_bucket("2001:db8:1:2::1")
    assert rate_bucket("203.0.113.9") == "203.0.113.9" and rate_bucket("::1") == "::1"


def test_distributed_remote_guessing_is_capped_but_the_owner_at_the_machine_still_gets_in(tmp_path):
    auth = _owner(tmp_path)
    for n in range(30):
        assert auth.authenticate("zak", "wrong-password-1", ip_address=f"198.51.{n}.7", remote=True) is None
    with pytest.raises(ValueError):
        auth.authenticate("zak", PASSWORD, ip_address="192.0.2.200", remote=True)     # remote: capped
    assert auth.authenticate("zak", PASSWORD, ip_address="127.0.0.1") is not None   # at the machine: never


def test_an_undeclared_proxy_never_shares_the_owners_local_bucket(server, monkeypatch):  # noqa: F811
    """Finding 3: with HOOD_TRUSTED_PROXY unset, proxied failures landed in login:127.0.0.1:<user>."""
    port, auth, _ = server
    auth.initialize_root_owner("zak", "Zak", PASSWORD)
    monkeypatch.delenv("HOOD_TRUSTED_PROXY")
    for _ in range(6):
        call(port, "/api/auth/login", {"username": "zak", "password": "wrong-password-1"}, via_proxy("203.0.113.66"))
    assert call(port, "/api/auth/login", {"username": "zak", "password": PASSWORD})[0] == 200   # local owner


def test_build_machine_data_never_enters_the_image():
    """Finding 2: artifacts/.vault_key, logs and old stores were copied into the image and imported."""
    ignore = (ROOT / ".dockerignore").read_text().split()
    for entry in ("artifacts/", "**/.vault_key", "**/.receipt_key", "**/.token_key", "**/*.log"):
        assert entry in ignore
    assert "HOOD_SKIP_LEGACY_MIGRATION=1" in (ROOT / "Dockerfile").read_text()
    from packages.config import paths
    import inspect
    assert "[REPO_ROOT]" in inspect.getsource(paths.migrate_legacy_data)   # the start folder isn't trusted


def price(i, o, audio=None):
    return ModelPrice(i, o, "2026-10-09", "test", audio)


class Scripted(BaseModelProvider):
    def __init__(self, outcome, candidates=("gemini-a",)):
        super().__init__(ProviderName.GEMINI, enabled=True)
        self.outcome, self.candidates = outcome, list(candidates)

    def is_healthy(self):
        return True

    def resolve_model(self, request):
        return self.candidates[0]

    def candidate_models(self, request):
        return self.candidates

    def invoke(self, request):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _router(provider, table):
    router = ModelRouter(SystemConfig(), price_table={"gemini": table})
    router.providers = {ProviderName.GEMINI: provider}
    return router


def test_a_rate_limit_after_a_possibly_billed_timeout_is_charged_not_released():
    err = ProviderRateLimitError("429 after an earlier timeout")
    err.possibly_billed = True
    router = _router(Scripted(err), {"gemini-a": price(0.001, 0.002)})
    with pytest.raises(Exception):
        router.invoke(ModelRequest(prompt="x" * 300, task_id="t", max_tokens=1000))
    assert router.cost_controller.get_call_history("t")[0]["cost_measured"] is False
    assert router.cost_controller.daily_spend_usd > 0


def test_an_answer_after_uncertain_attempts_costs_at_least_the_reservation():
    resp = ModelResponse(text="ok", provider=ProviderName.GEMINI, model_name="gemini-a", latency_ms=1,
                         usage=ModelUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                         requested_model="gemini-a", uncertain_attempts=2)
    router = _router(Scripted(resp), {"gemini-a": price(0.001, 0.002)})
    out = router.invoke(ModelRequest(prompt="hello", task_id="t", max_tokens=1000))
    assert out.usage.estimated_cost_usd >= 0.002 - 1e-9          # >= max_tokens * output price


def test_reservation_uses_the_candidate_dearest_for_this_request():
    router = ModelRouter(SystemConfig(), price_table={"gemini": {
        "gemini-in": price(0.010, 0.001), "gemini-out": price(0.001, 0.009)}})
    provider = Scripted(None, candidates=("gemini-in", "gemini-out"))
    _, chosen, _ = router._price_for(ProviderName.GEMINI, provider, ModelRequest(prompt="hi", max_tokens=4000))
    assert chosen.output_per_1k_usd == 0.009          # short prompt, long answer: output price dominates


def test_paid_voice_transcription_needs_an_audio_price(tmp_path, monkeypatch):
    """Finding 6: audio was reserved and billed at the (several times lower) text rate."""
    import base64
    from packages.auth.vault import SecretVault
    from services.voice.cloud import GeminiVoice, VoiceRefused, stt_model
    vault = SecretVault(tmp_path / "v.enc")
    vault.set_secret("gemini", "api_key", "AIza" + "z" * 35)
    router = ModelRouter(SystemConfig(), vault=vault, price_table={"gemini": {stt_model(): price(0.0003, 0.0025)}})
    voice = GeminiVoice(router, data_dir=tmp_path / "voice")
    voice.set_consent("owner", True)
    sent = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError))
    audio = base64.b64encode(b"\0" * 2000).decode()
    with pytest.raises(VoiceRefused, match="No audio input price"):
        voice.transcribe("owner", audio, "audio/webm")
    assert sent == []                                       # refused before anything was sent
    router.price_table = {"gemini": {stt_model(): price(0.0, 0.0)}}   # a free key: still allowed
    try:
        voice.transcribe("owner", audio, "audio/webm")
    except VoiceRefused as exc:
        assert "audio input price" not in str(exc)
    except Exception:  # noqa: BLE001 - the faked network refuses; the price gate let it through
        pass


def test_a_ledger_write_failure_never_loses_a_paid_answer(tmp_path, monkeypatch):
    cc = CostController(BudgetSettings(), ledger_path=tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(cc, "_insert", lambda record: (_ for _ in ()).throw(__import__("sqlite3").OperationalError("disk full")))
    rid = cc.reserve("t", 0.01)
    assert cc.settle(rid, ModelUsage(total_tokens=5, estimated_cost_usd=0.01), cost_measured=True) == 0.01
    assert cc.daily_spend_usd == pytest.approx(0.01) and "disk full" in cc.get_summary()["ledger_error"]


def test_mission_caps_survive_a_restart(tmp_path):
    budgets = BudgetSettings(max_task_spend_usd=1.0, max_task_model_calls=3)
    ledger = tmp_path / "ledger.sqlite3"
    cc = CostController(budgets, ledger_path=ledger)
    for _ in range(3):
        cc.settle(cc.reserve("mission-1", 0.1), ModelUsage(total_tokens=1, estimated_cost_usd=0.1), cost_measured=True)
    again = CostController(budgets, ledger_path=ledger)
    assert again.task_calls["mission-1"] == 3 and again.task_spend["mission-1"] == pytest.approx(0.3)
    from services.model_gateway.cost_controller import BudgetExceededError
    with pytest.raises(BudgetExceededError, match="call cap"):
        again.reserve("mission-1", 0.01)
