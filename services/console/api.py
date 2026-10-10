"""Console adapter resources for the HOOD NEXT UI (docs: ui/hood-next merge contract).

Every response carries ``generated_at`` and ``provenance``; every list is scoped to the
caller. Missing telemetry is reported as "unknown", never as zero or healthy.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from services.capabilities.registry import get_capability_inventory
from ui.routes import SERVICES, Stream, route

VERSION = "hood-console/1"
LAYOUT_MAX_BYTES = 16 * 1024
_layout_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir() -> Path:
    return Path(os.environ.get("HOOD_DATA_DIR") or (Path.home() / ".hood")) / "console"


def _engine(ctx):
    return ctx.services.get("agents")


def _is_root(ctx) -> bool:
    return getattr(ctx.session.role, "value", ctx.session.role) == "ROOT_OWNER"


def _provider_health(engine) -> List[Dict[str, Any]]:
    """Provider state from what this process observed (router) plus configuration.

    States: not_configured (no key) · needs_pricing (key, but calls are refused as
    unknown cost) · configured (ready, not used since HOOD started) ·
    verified_online (last call succeeded, within 30 min) · degraded (last call
    failed). Failures from earlier runs (e.g. before a key was set) no longer
    count; the durable ledger still supplies the last success for history.
    """
    router = SERVICES.get("router")
    out = []
    for name in ["gemini", "openai", "local"]:
        entry = {"id": name, "configured": None, "state": "unknown", "last_success_at": None,
                 "last_failure_at": None, "last_error": None, "last_model": None,
                 "provenance": "model router (this run) + agent-engine spend ledger"}
        observed: Dict[str, Any] = {}
        priced = None
        if router is not None:
            from packages.contracts import ModelRequest, ProviderName
            pname = ProviderName(name)
            provider = router.providers.get(pname)
            entry["configured"] = bool(provider and provider.enabled and provider.is_healthy())
            if entry["configured"]:
                try:
                    _, price, billing = router._price_for(pname, provider, ModelRequest(prompt="status"))
                    priced = price is not None or not billing
                except Exception:
                    priced = False
            observed = router.observed_health(pname) if hasattr(router, "observed_health") else {}
        if engine is not None:
            with sqlite3.connect(engine.db_path) as db:
                ok = db.execute("SELECT created, model FROM spend WHERE provider=? AND settled IS NOT NULL "
                                "AND actual_usd IS NOT NULL AND COALESCE(simulated,0)=0 ORDER BY created DESC LIMIT 1",
                                (name,)).fetchone()
            if ok:
                entry["last_success_at"], entry["last_model"] = ok[0], ok[1]
        seen_ok, seen_bad = observed.get("last_success_at"), observed.get("last_failure_at")
        if seen_ok and (not entry["last_success_at"] or seen_ok > entry["last_success_at"]):
            entry["last_success_at"], entry["last_model"] = seen_ok, observed.get("last_model")
        if seen_bad:
            entry["last_failure_at"], entry["last_error"] = seen_bad, observed.get("last_error")

        if entry["configured"] is False:
            entry["state"] = "not_configured"
        elif entry["configured"] and priced is False:
            entry["state"] = "needs_pricing"
        elif seen_bad and (not seen_ok or seen_bad > seen_ok):
            entry["state"] = "degraded"
        elif seen_ok:
            recent = datetime.fromisoformat(seen_ok) > datetime.now(timezone.utc) - timedelta(minutes=30)
            entry["state"] = "verified_online" if recent else "configured"
        elif entry["configured"]:
            entry["state"] = "configured"
        out.append(entry)
    return out


@route("GET", r"/api/console/overview", permission="VIEW_TELEMETRY")
def overview(ctx):
    engine = _engine(ctx)
    missions, counts, spend = [], {}, None
    if engine is not None:
        missions = engine.list(ctx.user_id)
        for m in missions:
            counts[m["state"]] = counts.get(m["state"], 0) + 1
        spend = round(sum(engine.spend(ctx.user_id, m["id"])["total_usd"] for m in missions[:100]), 6)
    approvals = SERVICES.get("approvals")
    pending = None
    if approvals is not None:
        pending = sum(1 for a in approvals.list_pending()
                      if _is_root(ctx) or getattr(a, "principal", None) in (None, ctx.user_id))
    stop = SERVICES.get("emergency_stop")
    x = SERVICES.get("x")
    inventory = get_capability_inventory()
    live_counts: Dict[str, int] = {}
    for item in inventory["items"]:
        state = item["live"]["state"]
        live_counts[state] = live_counts.get(state, 0) + 1
    return {
        "generated_at": _now(), "provenance": "server", "version": VERSION,
        "missions": {"available": engine is not None, "by_state": counts, "recent": missions[:10]},
        "approvals_pending": pending,
        "spend_usd": spend, "spend_note": "Sum of this user's agent-mission ledger; not a provider invoice.",
        "providers": _provider_health(engine),
        "emergency_stop": stop.tool_gateway.stop_latch.snapshot() if stop is not None and stop.tool_gateway else None,
        "x": ({"state": x.get_status().get("state"), "is_active": x.get_status().get("is_active")}
              if x is not None else {"state": "unknown", "is_active": None}),
        "capabilities": {"by_live_state": live_counts, "total": len(inventory["items"])},
    }


AGENT_ROLES = [
    {"id": "planner", "name": "Planner", "model_class": "STANDARD",
     "duty": "Turns an objective into a validated task graph and interface contract",
     "writes": [], "tools": []},
    {"id": "engineer", "name": "Engineer", "model_class": "STANDARD",
     "duty": "Writes application code and unit tests", "writes": ["app/", "tests/"], "tools": ["sandbox write"]},
    {"id": "qa", "name": "QA", "model_class": "STANDARD",
     "duty": "Writes independent acceptance tests from the objective", "writes": ["qa_tests/"],
     "tools": ["sandbox write"]},
    {"id": "reviewer", "name": "Reviewer", "model_class": "STANDARD",
     "duty": "Reports defects (advisory)", "writes": [], "tools": []},
    {"id": "verifier", "name": "Verifier", "model_class": None,
     "duty": "Deterministic checks: compile, unit tests, acceptance tests (not an AI)", "writes": [],
     "tools": ["python -m compileall", "python -m pytest"]},
]


@route("GET", r"/api/console/agents", permission="VIEW_PROJECT_DATA")
def agents(ctx):
    engine = _engine(ctx)
    router = SERVICES.get("router")
    roles = []
    for role in AGENT_ROLES:
        entry = dict(role)
        entry["model"] = None
        if role["model_class"] and router is not None:
            from packages.contracts import ModelClass, ModelRequest, ProviderName
            provider = router.providers.get(ProviderName.GEMINI)
            if provider is not None and provider.enabled:
                req = ModelRequest(prompt="", model_class=ModelClass(role["model_class"]))
                entry["model"] = {"provider": "gemini", "primary": provider.resolve_model(req),
                                  "fallbacks": provider.candidate_models(req)[1:]}
        entry["observed"] = {"calls": None, "running_tasks": None}
        if engine is not None and role["id"] not in ("planner", "verifier"):
            with sqlite3.connect(engine.db_path) as db:
                running = db.execute("SELECT COUNT(*) FROM tasks t JOIN missions m ON m.id=t.mission_id "
                                     "WHERE m.owner=? AND t.role=? AND t.state='RUNNING'",
                                     (ctx.user_id, role["id"])).fetchone()[0]
                calls = db.execute("SELECT COUNT(*) FROM spend s JOIN tasks t ON t.mission_id=s.mission_id AND "
                                   "t.task_id=s.task_id JOIN missions m ON m.id=s.mission_id WHERE m.owner=? "
                                   "AND t.role=?", (ctx.user_id, role["id"])).fetchone()[0]
            entry["observed"] = {"calls": calls, "running_tasks": running}
        roles.append(entry)
    return {"generated_at": _now(), "provenance": "server", "roles": roles,
            "providers": _provider_health(engine)}


@route("GET", r"/api/console/graph", permission="VIEW_PROJECT_DATA")
def graph(ctx):
    engine = _engine(ctx)
    limit = min(int((ctx.query.get("limit") or ["20"])[0]), 100)
    nodes = [{"id": "hood", "type": "core", "label": "HOOD core", "state": "available", "provenance": "server"}]
    edges = []
    for item in get_capability_inventory()["items"]:
        nid = "cap:" + item["id"]
        nodes.append({"id": nid, "type": "capability", "label": item["name"], "group": item["category"],
                      "state": item["live"]["state"], "detail": item["live"].get("detail"), "provenance": "registry"})
        edges.append({"from": "hood", "to": nid, "type": "has_capability"})
    for role in AGENT_ROLES:
        nid = "agent:" + role["id"]
        nodes.append({"id": nid, "type": "agent", "label": role["name"], "state": "available", "provenance": "engine"})
        edges.append({"from": "cap:orchestrator", "to": nid, "type": "uses_agent"})
    if engine is not None:
        for m in engine.list(ctx.user_id)[:limit]:
            mid = "mission:" + m["id"]
            nodes.append({"id": mid, "type": "mission", "label": m["objective"][:80], "state": m["state"],
                          "updated": m["updated"], "provenance": "agent-engine"})
            edges.append({"from": "cap:orchestrator", "to": mid, "type": "has_mission"})
            for t in engine.status(ctx.user_id, m["id"])["tasks"]:
                tid = f"task:{m['id']}:{t['task_id']}"
                nodes.append({"id": tid, "type": "task", "label": t["title"], "state": t["state"],
                              "provenance": "agent-engine"})
                edges.append({"from": mid, "to": tid, "type": "has_task"})
                edges.append({"from": tid, "to": "agent:" + t["role"], "type": "assigned_role"})
                for dep in t["depends_on"]:
                    edges.append({"from": f"task:{m['id']}:{dep}", "to": tid, "type": "depends_on"})
    return {"generated_at": _now(), "provenance": "server", "nodes": nodes, "edges": edges}


def _cursor(ctx) -> int:
    raw = (ctx.query.get("cursor") or ["0"])[0]
    if not raw.isdigit():
        raise ValueError("cursor must be a non-negative integer")
    return int(raw)


@route("GET", r"/api/console/events", permission="VIEW_PROJECT_DATA")
def events(ctx):
    engine = _engine(ctx)
    items = engine.events_since(ctx.user_id, _cursor(ctx)) if engine is not None else []
    return {"generated_at": _now(), "provenance": "agent-engine events", "events": items,
            "cursor": items[-1]["seq"] if items else _cursor(ctx)}


@route("GET", r"/api/console/events/stream", permission="VIEW_PROJECT_DATA")
def event_stream(ctx):
    engine = _engine(ctx)
    cursor = _cursor(ctx)
    user = ctx.user_id

    def generate():
        nonlocal cursor
        while True:
            batch = engine.events_since(user, cursor, 100) if engine is not None else []
            if not batch:
                yield None
                time.sleep(1.0)
                continue
            for item in batch:
                cursor = item["seq"]
                yield item["seq"], "mission_event", item
    return Stream(generate())


def _layout_path(user_id: str) -> Path:
    safe = "".join(c for c in user_id if c.isalnum() or c in "_-")[:80]
    return _data_dir() / "layouts" / f"{safe}.json"


@route("GET", r"/api/console/layout", permission="VIEW_TELEMETRY")
def get_layout(ctx):
    path = _layout_path(ctx.user_id)
    layout = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    return {"layout": layout, "provenance": "server (per user)"}


@route("POST", r"/api/console/layout", permission="VIEW_TELEMETRY")
def save_layout(ctx):
    layout = ctx.payload.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("layout must be an object")
    widgets = layout.get("widgets", [])
    if not isinstance(widgets, list) or len(widgets) > 40:
        raise ValueError("layout.widgets must be a list of at most 40 entries")
    for w in widgets:
        if not isinstance(w, dict) or set(w) - {"id", "span", "hidden", "height", "order"}:
            raise ValueError("Unexpected widget fields")
        if not isinstance(w.get("id"), str) or len(w["id"]) > 40:
            raise ValueError("Widget id must be a short string")
    raw = json.dumps(layout, sort_keys=True)
    if len(raw.encode()) > LAYOUT_MAX_BYTES:
        raise ValueError("Layout too large")
    path = _layout_path(ctx.user_id)
    with _layout_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, path)
    return {"saved": True}


@route("GET", r"/api/console/governance", permission="VIEW_TELEMETRY")
def governance(ctx):
    stop = SERVICES.get("emergency_stop")
    x = SERVICES.get("x")
    status = x.get_status() if x is not None else {}
    return {
        "generated_at": _now(), "provenance": "server",
        "x": {"state": status.get("state", "unknown"), "is_active": status.get("is_active"),
              "activation": "Only via an L4 approval requested in chat by the authenticated Root Owner; "
                            "there is no toggle."},
        "emergency_stop": stop.tool_gateway.stop_latch.snapshot() if stop is not None and stop.tool_gateway else None,
        "policy": {"approvals": "exact action: tool, target, task, parameter hash, principal, expiry, single use",
                   "simulated_output": "refused by the live agent engine"},
    }
