"""Console adapter resources: tenancy, truthful unknowns, SSE replay, layout validation."""
import json
import socket
import time
import urllib.request

import pytest

from tests.agents.test_agent_engine import OBJECTIVE
from tests.agents.test_agent_http import stack  # noqa: F401 (fixture)
from tests.hardening.test_remediation import request


def _get(base, path, cookie):
    status, body, _ = request(base, path, cookie)
    return status, (json.loads(body) if body else None)


def test_overview_is_scoped_and_truthful(stack):  # noqa: F811
    base, c, engine, _ = stack
    engine.create_mission("user_root_owner_01", OBJECTIVE)
    status, owner_view = _get(base, "/api/console/overview", c["owner"])
    assert status == 200 and owner_view["missions"]["by_state"] == {"AWAITING_PLAN_APPROVAL": 1}
    assert owner_view["provenance"] == "server" and owner_view["generated_at"]
    # No router attached in this fixture: provider configuration is unknown, not "healthy".
    assert all(p["configured"] is None and p["state"] == "unknown" for p in owner_view["providers"])
    _, admin_view = _get(base, "/api/console/overview", c["admin2"])
    assert admin_view["missions"]["by_state"] == {} and admin_view["missions"]["recent"] == []
    assert request(base, "/api/console/overview")[0] == 401


def test_graph_contains_only_callers_missions(stack):  # noqa: F811
    base, c, engine, _ = stack
    mine = engine.create_mission("user_root_owner_01", OBJECTIVE)["mission_id"]
    _, g = _get(base, "/api/console/graph", c["owner"])
    ids = {n["id"] for n in g["nodes"]}
    assert "mission:" + mine in ids and "cap:orchestrator" in ids and "agent:verifier" in ids
    assert all(e["from"] in ids or e["from"].startswith("task:") for e in g["edges"])
    _, other = _get(base, "/api/console/graph", c["admin2"])
    assert not any(n["type"] == "mission" for n in other["nodes"])
    assert request(base, "/api/console/graph", c["viewer"])[0] == 200  # viewers may see structure only


def test_events_poll_and_stream_replay_from_cursor(stack):  # noqa: F811
    base, c, engine, _ = stack
    engine.create_mission("user_root_owner_01", OBJECTIVE)
    _, first = _get(base, "/api/console/events?cursor=0", c["owner"])
    assert first["events"] and first["cursor"] == first["events"][-1]["seq"]
    _, none_new = _get(base, f"/api/console/events?cursor={first['cursor']}", c["owner"])
    assert none_new["events"] == []
    assert request(base, "/api/console/events?cursor=-1", c["owner"])[0] == 400
    _, admin = _get(base, "/api/console/events?cursor=0", c["admin2"])
    assert admin["events"] == []
    # SSE: replay everything after cursor 0, then a new event arrives live.
    req = urllib.request.Request(base + "/api/console/events/stream?cursor=0", headers={"Cookie": c["owner"]})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.headers["Content-Type"] == "text/event-stream"
        seen = []
        engine.create_mission("user_root_owner_01", OBJECTIVE + " second")
        deadline = time.time() + 8
        while time.time() < deadline and len(seen) < len(first["events"]) + 2:
            line = resp.readline().decode()
            if line.startswith("id: "):
                seen.append(int(line[4:]))
        assert seen == sorted(seen) and seen[0] == first["events"][0]["seq"]
        assert len(seen) >= len(first["events"]) + 2


def test_layout_is_per_user_and_validated(stack):  # noqa: F811
    base, c, _, _ = stack
    layout = {"widgets": [{"id": "command", "span": 8}, {"id": "agents", "hidden": True}]}
    assert request(base, "/api/console/layout", c["owner"], {"layout": layout})[0] == 200
    assert _get(base, "/api/console/layout", c["owner"])[1]["layout"] == layout
    assert _get(base, "/api/console/layout", c["admin2"])[1]["layout"] is None
    bad = {"widgets": [{"id": "x", "onclick": "alert(1)"}]}
    assert request(base, "/api/console/layout", c["owner"], {"layout": bad})[0] == 400
    huge = {"widgets": [{"id": "w" * 41}]}
    assert request(base, "/api/console/layout", c["owner"], {"layout": huge})[0] == 400


def test_governance_has_no_activation_switch(stack):  # noqa: F811
    base, c, _, _ = stack
    _, gov = _get(base, "/api/console/governance", c["viewer"])
    assert "no toggle" in gov["x"]["activation"]
    assert gov["emergency_stop"]["engaged"] is False


def test_graph_groups_every_capability_into_an_owner_facing_area(stack):  # noqa: F811
    base, c, engine, _ = stack
    _, g = _get(base, "/api/console/graph", c["owner"])
    areas = {n["id"] for n in g["nodes"] if n["type"] == "area"}
    assert {"area:talk", "area:build", "area:safety", "area:models"} <= areas
    caps = [n for n in g["nodes"] if n["type"] == "capability"]
    assert caps and all("area:" + n["area"] in areas for n in caps)
    edges = {(e["from"], e["to"]) for e in g["edges"]}
    assert all(("area:" + n["area"], n["id"]) in edges for n in caps)
    by_id = {n["id"]: n for n in caps}
    assert by_id["cap:conversation"]["area"] == "talk" and by_id["cap:gemini"]["page"] == "Intelligence"
    assert by_id["cap:orchestrator"]["description"]
