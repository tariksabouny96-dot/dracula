"""Intelligence page, live data (Phase 4): what HOOD actually uses and spends, and how its work goes.

Every number comes from a store HOOD writes while working (spend ledger, agent-engine database,
self-repair database, router observations, toolbox state); nothing is estimated or invented, and
anything not measured yet is reported as such.
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from ui.routes import SERVICES, route


def _models(router) -> Dict[str, Any]:
    from packages.contracts import ModelClass, ModelRequest, ProviderName
    out: Dict[str, Any] = {"provider": "gemini", "classes": {}, "prices": {}}
    gem = router.providers.get(ProviderName.GEMINI) if router is not None else None
    if gem is None:
        return out
    table = (router.price_table or {}).get("gemini", {})
    for cls, role in ((ModelClass.FAST, "chat"), (ModelClass.STANDARD, "missions"), (ModelClass.DEEP, "deep / self-repair")):
        names = gem.candidate_models(ModelRequest(prompt="", model_class=cls))
        out["classes"][cls.value] = {"role": role, "model": names[0], "fallbacks": names[1:]}
        for n in names:
            p = table.get(n)
            out["prices"][n] = None if p is None else {
                "input_per_1k_usd": p.input_per_1k_usd, "output_per_1k_usd": p.output_per_1k_usd,
                "audio_input_per_1k_usd": p.audio_input_per_1k_usd, "as_of": p.as_of, "source": p.source}
    out["observed"] = router.observed_health(ProviderName.GEMINI) if hasattr(router, "observed_health") else {}
    return out


def _spend(router) -> Dict[str, Any]:
    cc = getattr(router, "cost_controller", None)
    if cc is None:
        return {"available": False}
    summary = cc.get_summary()
    summary["ledger"] = bool(summary.get("ledger"))        # kept or not; the file path stays on the server
    out: Dict[str, Any] = {"available": True, **summary, "by_model": [], "recent": [], "days": []}
    if not getattr(cc, "ledger_path", None) or not cc.ledger_path.exists():
        out["note"] = "No durable spend ledger yet (no paid or priced call recorded on this machine)."
        return out
    since = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    try:
        with sqlite3.connect(str(cc.ledger_path), timeout=10) as db:
            out["by_model"] = [{"model": m, "calls": c, "prompt_tokens": p or 0, "completion_tokens": o or 0,
                                "cost_usd": round(s or 0.0, 6)} for m, c, p, o, s in db.execute(
                "SELECT model, COUNT(*), SUM(prompt_tokens), SUM(completion_tokens), SUM(cost_usd) FROM calls "
                "WHERE day >= ? GROUP BY model ORDER BY SUM(cost_usd) DESC, COUNT(*) DESC", (since,))]
            out["days"] = [{"day": d, "calls": c, "cost_usd": round(s or 0.0, 6)} for d, c, s in db.execute(
                "SELECT day, COUNT(*), SUM(cost_usd) FROM calls WHERE day >= ? GROUP BY day ORDER BY day", (since,))]
            out["recent"] = [{"ts": ts, "kind": _kind(task), "model": m, "tokens": t or 0, "cost_usd": round(s or 0.0, 6),
                              "measured": bool(meas)} for ts, task, m, t, s, meas in db.execute(
                "SELECT ts, task_id, model, total_tokens, cost_usd, cost_measured FROM calls ORDER BY ts DESC LIMIT 15")]
    except sqlite3.Error as exc:
        out["note"] = f"Spend ledger unreadable: {exc}"
    return out


def _kind(task_id) -> str:
    t = str(task_id or "")
    for prefix, label in (("conv_", "chat"), ("draft_", "mission brief"), ("selfrepair:", "self-repair")):
        if t.startswith(prefix):
            return label
    return "mission" if t else "voice / other"


def _missions(engine, owner: str) -> Dict[str, Any]:
    if engine is None:
        return {"available": False}
    with sqlite3.connect(engine.db_path, timeout=10) as db:
        rows = db.execute("SELECT state, profile, created, updated FROM missions WHERE owner=?", (owner,)).fetchall()
    by_state: Dict[str, int] = {}
    by_kind: Dict[str, Dict[str, int]] = {}
    durations: List[float] = []
    for state, profile, created, updated in rows:
        by_state[state] = by_state.get(state, 0) + 1
        kind = by_kind.setdefault(profile or "python_app", {})
        kind[state] = kind.get(state, 0) + 1
        if state == "COMPLETED":
            try:
                durations.append((datetime.fromisoformat(updated) - datetime.fromisoformat(created)).total_seconds())
            except (TypeError, ValueError):
                pass
    finished = sum(by_state.get(s, 0) for s in ("COMPLETED", "FAILED", "UNVERIFIED"))
    return {"available": True, "total": len(rows), "by_state": by_state, "by_kind": by_kind,
            "success_rate": round(by_state.get("COMPLETED", 0) / finished, 3) if finished else None,
            "median_completed_seconds": round(statistics.median(durations)) if durations else None}


def _environment() -> Dict[str, Any]:
    from services.agents.sandbox import sandbox_problem
    problem = sandbox_problem()
    out: Dict[str, Any] = {"sandbox_ready": problem is None, "sandbox_problem": problem}
    tb = SERVICES.get("toolbox")
    if tb is not None:
        try:
            tools = tb.status()["tools"]
            out["tools"] = {"known": len(tools), "approved": sum(1 for t in tools if t.get("approved")),
                            "installed": sum(1 for t in tools if t.get("installed"))}
        except Exception as exc:  # noqa: BLE001 - shown, never fatal for the page
            out["tools"] = {"error": str(exc)[:200]}
    return out


def _self_repair() -> Dict[str, Any]:
    svc = SERVICES.get("selfrepair")
    if svc is None:
        return {"available": False}
    counts: Dict[str, int] = {}
    for r in svc.list():
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"available": True, "by_state": counts}


@route("GET", r"/api/intelligence/live", permission="VIEW_TELEMETRY")
def intelligence_live(ctx):
    router = SERVICES.get("router")
    return {"measured_at": datetime.now(timezone.utc).isoformat(), "models": _models(router),
            "spend": _spend(router), "missions": _missions(SERVICES.get("agents"), ctx.user_id),
            "environment": _environment(), "self_repair": _self_repair(),
            "sources": "spend ledger, agent-engine database, router observations, toolbox and self-repair state"}
