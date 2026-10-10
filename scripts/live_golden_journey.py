"""Run the golden journey against the configured live provider and record evidence.

    HOOD_GEMINI_CREDENTIAL=proxy HOOD_MODEL_PRICING=config/model_pricing.free-tier.json \
        python scripts/live_golden_journey.py --out release/evidence/live

Creates a mission, auto-approves the exact plan hash (the operator ran this script, which is
the approval), runs it, retries provider outages up to --retries times, then writes a JSON
record with commit, models, token usage, every verification verdict and the artifact hash.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OBJECTIVE = ("Build a small responsive notes web app in Python (standard library only): an in-memory notes store "
             "with create, read, update, delete and list operations, input validation (title 1-200 characters), and "
             "a JSON HTTP API (GET/POST /notes, GET/PUT/DELETE /notes/<id>) served by http.server, plus a "
             "mobile-friendly HTML index page at /.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "release" / "evidence" / "live"))
    parser.add_argument("--data-dir", default=os.environ.get("HOOD_DATA_DIR", str(ROOT / ".hood-live")))
    parser.add_argument("--budget", type=float, default=1.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--objective", default=OBJECTIVE)
    args = parser.parse_args()
    from packages.config import load_config
    from packages.security import StopLatch
    from services.agents import AgentEngine
    from services.model_gateway.cost_controller import CostController
    from services.model_gateway.router import ModelRouter
    config = load_config(Path("hood.config.yaml"))
    router = ModelRouter(config, CostController(config.budgets))
    engine = AgentEngine(Path(args.data_dir) / "agents", router=router, stop_latch=StopLatch())
    owner = "live-journey-operator"
    started = time.time()
    status = engine.create_mission(owner, args.objective, args.budget)
    mid = status["mission_id"]
    if status["state"] == "AWAITING_PLAN_APPROVAL":
        engine.approve_plan(owner, mid, status["plan_sha256"], approver="script-operator")
        status = engine.run(owner, mid, max_steps=80)
        for _ in range(args.retries):
            if status["state"] != "BLOCKED":
                break
            time.sleep(30)
            engine.retry_blocked(owner, mid, "script-operator")
            status = engine.run(owner, mid, max_steps=80)
    calls = engine.spend(owner, mid)["calls"]
    record = {
        "kind": "LIVE_PROVIDER_GOLDEN_JOURNEY",
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        "generated_at": datetime.now(timezone.utc).isoformat(), "duration_s": round(time.time() - started, 1),
        "mission_id": mid, "state": status["state"], "objective_verified": status["objective_verified"],
        "provider_mode": status["provider_mode"], "error": status["error"], "repairs": status["repairs"],
        "plan": status["plan"], "tasks": [{k: t[k] for k in ("task_id", "role", "state", "attempts", "error")}
                                          for t in status["tasks"]],
        "verifications": [json.loads(e["detail"]) for e in engine.events(owner, mid) if e["kind"] == "STATE"
                          and "verdict" in e["detail"]],
        "last_verification": status["last_verification"], "artifact": status["artifact"],
        "model_calls": [{k: c[k] for k in ("task_id", "provider", "model", "simulated", "reserved_usd", "actual_usd",
                                           "measured")} for c in calls],
        "spend_usd": engine.spend(owner, mid)["total_usd"],
        "receipts_valid": engine.verify_receipts(owner, mid)["valid"],
    }
    if status["artifact"]:
        name, data, digest = engine.artifact(owner, mid)
        out_zip = Path(args.out) / name
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        out_zip.write_bytes(data)
        record["artifact_file"] = str(out_zip.relative_to(ROOT)) if out_zip.is_relative_to(ROOT) else str(out_zip)
        record["artifact_sha256_recomputed"] = hashlib.sha256(data).hexdigest()
    out = Path(args.out) / f"{mid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("mission_id", "state", "objective_verified", "provider_mode", "repairs",
                                             "duration_s", "spend_usd")} | {"record": str(out)}, indent=2))
    return 0 if status["state"] == "COMPLETED" else 1


if __name__ == "__main__":
    sys.exit(main())
