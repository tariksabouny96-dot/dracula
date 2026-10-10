"""Evidence-consuming, fail-closed HOOD preproduction gate (blueprint section 8, G0-G7).

It runs the full test suite once, binds every result to the exact Git commit and
host, maps results onto the 26-capability register (audit/CAPABILITY_ACCEPTANCE.json)
and the finding ledger (audit/FINDINGS_F01_F36.csv), then decides:

    GO_FOR_SUPERVISED_PREPRODUCTION  only if every gate G0-G7 passes, or
    NO_GO                            with machine-readable reasons.

There is no override flag. Skipped tests are missing evidence, never passes.
Local tests can at most prove each capability's declared ``local_evidence_level``;
target-machine, live-provider and staging levels need their own evidence files.
Exit code: 0 for GO, 1 for NO_GO.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "audit" / "CAPABILITY_ACCEPTANCE.json"
FINDINGS = ROOT / "audit" / "FINDINGS_F01_F36.csv"
STAGING_EVIDENCE = ROOT / "release" / "staging" / "STAGING_EVIDENCE.json"
INDEPENDENT_REVIEW = ROOT / "audit" / "INDEPENDENT_REVIEW.json"
EVIDENCE_DIR = ROOT / "release" / "evidence"

LEVELS = ["NOT_IMPLEMENTED", "DEFERRED", "BLOCKED", "FAILED", "SIMULATED", "IMPLEMENTED_UNVERIFIED",
          "TESTED_WITH_STUB", "VERIFIED_LOCAL", "VERIFIED_TARGET", "VERIFIED_STAGING"]
RANK = {name: i for i, name in enumerate(LEVELS)}
CLOSED_FINDING_STATES = {"FIXED_VERIFIED_LOCAL", "FIXED_VERIFIED_STAGING", "NOT_APPLICABLE"}
SECURITY_SUITES = ("tests/security/", "tests/adversarial/", "tests/auth/")
RECOVERY_TESTS = ("test_crash_after_model_call_reapplies_without_new_model_call", "test_repeated_crash_blocks_for_operator",
                  "test_cancel_prevents_late_completion", "test_emergency_stop_blocks_engine",
                  "test_unsettled_spend_is_charged_on_recovery", "test_parallel_reservations_cannot_overspend",
                  "test_mission_budget_blocks_before_any_paid_call", "test_stop_latch_survives_restart")


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_suite(junit: Path, timeout: int) -> dict:
    cmd = [sys.executable, "-m", "pytest", "-q", "-rs", f"--junitxml={junit}"]
    try:
        run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        return {"command": cmd, "exit_code": run.returncode, "output_tail": (run.stdout + run.stderr)[-6000:]}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit_code": None, "output_tail": "TIMEOUT"}


def parse_junit(junit: Path) -> dict:
    """Map 'tests/dir/file.py::test_name[param]' -> passed|failed|skipped."""
    results = {}
    if not junit.is_file():
        return results
    for case in ET.parse(junit).getroot().iter("testcase"):
        path = case.get("classname", "").replace(".", "/") + ".py"
        node = f"{path}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            results[node] = "failed"
        elif case.find("skipped") is not None:
            results[node] = "skipped"
        else:
            results[node] = "passed"
    return results


def matching(results: dict, test_id: str) -> dict:
    if "::" in test_id:
        return {k: v for k, v in results.items() if k == test_id or k.startswith(test_id + "[")}
    prefix = test_id if test_id.endswith(".py") else test_id.rstrip("/") + "/"
    return {k: v for k, v in results.items() if k.startswith(prefix)}


def evaluate_capability(cap: dict, results: dict) -> dict:
    hits = {}
    for test_id in cap["test_ids"]:
        hits.update(matching(results, test_id))
    failed = sorted(k for k, v in hits.items() if v == "failed")
    skipped = sorted(k for k, v in hits.items() if v == "skipped")
    passed = sorted(k for k, v in hits.items() if v == "passed")
    if not cap["test_ids"]:
        achieved = cap["implementation_status"]
    elif failed:
        achieved = "FAILED"
    elif not passed:
        achieved = "BLOCKED"
    else:
        achieved = cap["local_evidence_level"]
    required = cap["required_level"]
    if required == "DEFERRED":
        accepted = bool(cap.get("owner_deferral_approved")) and achieved != "FAILED"
        reason = ("Deferred and disabled by owner decision" if accepted
                  else "Optional capability needs an explicit owner deferral (and must stay disabled)")
    else:
        accepted = RANK.get(achieved, 0) >= RANK[required]
        reason = "Accepted" if accepted else f"Achieved {achieved}; requires {required}"
    return {"id": cap["id"], "scope": cap["scope"], "achieved_level": achieved, "required_level": required,
            "accepted": accepted, "reason": reason, "tests_passed": len(passed), "tests_failed": failed,
            "tests_skipped": skipped, "declared_blockers": cap["blockers"]}


def load_findings() -> list:
    if not FINDINGS.is_file():
        return []
    with FINDINGS.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def evidence_file_ok(path: Path, commit: str, keys: tuple) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"{path.relative_to(ROOT)} missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return False, f"{path.relative_to(ROOT)} is not valid JSON"
    if data.get("commit") != commit:
        return False, f"{path.relative_to(ROOT)} is for commit {data.get('commit')}, not {commit}"
    bad = [k for k in keys if data.get(k) != "PASS"]
    return (not bad), ("ok" if not bad else f"{path.relative_to(ROOT)} not PASS: {', '.join(bad)}")


def decide(register: dict, inventory_ids: set, results: dict, findings: list, *, commit: str, dirty: list,
           suite_exit: int | None) -> dict:
    """Pure decision function (unit-tested). Every gate must be PASS for GO."""
    caps = [evaluate_capability(c, results) for c in register["capabilities"]]
    reg_ids = {c["id"] for c in register["capabilities"]}
    gates = {}

    def gate(name, ok, reasons):
        gates[name] = {"status": "PASS" if ok else "FAIL", "reasons": reasons}

    g0 = []
    if dirty:
        g0.append(f"Working tree not clean ({len(dirty)} paths): evidence cannot be bound to a commit")
    if reg_ids != inventory_ids:
        g0.append(f"Register/registry drift: missing {sorted(inventory_ids - reg_ids)}, extra {sorted(reg_ids - inventory_ids)}")
    if not results:
        g0.append("No test results recorded")
    gate("G0_baseline", not g0, g0)

    open_p0 = [f["id"] for f in findings if f.get("priority") == "P0" and f.get("status") not in CLOSED_FINDING_STATES]
    sec = {k: v for k, v in results.items() if k.startswith(SECURITY_SUITES)}
    g1 = [f"Open P0 findings: {', '.join(open_p0)}"] if open_p0 else []
    if not findings:
        g1.append("Finding ledger missing")
    if not sec or any(v != "passed" for v in sec.values()):
        g1.append("Security suites not fully passing: " + ", ".join(k for k, v in sec.items() if v != "passed")[:500])
    if not INDEPENDENT_REVIEW.is_file():
        g1.append("No formal independent threat-model review recorded")
    gate("G1_p0_security", not g1, g1)

    orch = next(c for c in caps if c["id"] == "orchestrator")
    gate("G2_functional_core", orch["accepted"],
         [] if orch["accepted"] else [f"Golden journey with a live LLM not evidenced ({orch['reason']})"])

    rec = {k: v for k, v in results.items() if k.split("::")[-1].split("[")[0] in RECOVERY_TESTS}
    g3 = [] if rec and all(v == "passed" for v in rec.values()) and len(rec) >= len(RECOVERY_TESTS) else \
        ["Recovery/finance tests missing or failing: " + json.dumps(rec)[:500]]
    gate("G3_recovery_finance", not g3, g3)

    p1 = [c for c in caps if c["scope"] in ("P0", "P1") and not c["accepted"]]
    gate("G4_p1_coverage", not p1, [f"{c['id']}: {c['reason']}" for c in p1])

    failed = sorted(k for k, v in results.items() if v == "failed")
    skipped = sorted(k for k, v in results.items() if v == "skipped")
    g5 = []
    if suite_exit not in (0,):
        g5.append(f"pytest exit code {suite_exit}")
    if failed:
        g5.append(f"{len(failed)} failed: " + ", ".join(failed[:10]))
    if skipped:
        g5.append(f"{len(skipped)} skipped (environment-blocked evidence is not a pass): " + ", ".join(skipped[:10]))
    gate("G5_full_suite", not g5, g5)

    ok6, why6 = evidence_file_ok(STAGING_EVIDENCE, commit, ("deploy", "smoke", "backup_restore", "rollback"))
    gate("G6_deployment", ok6, [] if ok6 else [why6])
    ok7, why7 = evidence_file_ok(INDEPENDENT_REVIEW, commit, ("security_signoff", "architecture_signoff"))
    gate("G7_independent_review", ok7, [] if ok7 else [why7])

    go = all(g["status"] == "PASS" for g in gates.values())
    return {"decision": "GO_FOR_SUPERVISED_PREPRODUCTION" if go else "NO_GO", "gates": gates,
            "capabilities": caps,
            "accepted_capabilities": sum(c["accepted"] for c in caps), "total_capabilities": len(caps)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from services.capabilities.registry import get_capability_inventory

    commit = git("rev-parse", "HEAD")
    dirty = [line for line in git("status", "--porcelain").splitlines()
             if not line[3:].startswith(("release/evidence/", "artifacts/"))]
    out_dir = EVIDENCE_DIR / commit[:12]
    out_dir.mkdir(parents=True, exist_ok=True)
    junit = out_dir / "junit.xml"
    suite = run_suite(junit, args.timeout)
    results = parse_junit(junit)
    register = json.loads(REGISTER.read_text(encoding="utf-8"))
    inventory_ids = {item["id"] for item in get_capability_inventory()["items"]}
    verdict = decide(register, inventory_ids, results, load_findings(), commit=commit, dirty=dirty,
                     suite_exit=suite["exit_code"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "commit": commit, "working_tree_dirty": dirty,
        "host": {"platform": platform.platform(), "python": sys.version.split()[0], "machine": platform.machine()},
        "suite": {**suite, "junit": str(junit.relative_to(ROOT)),
                  "junit_sha256": sha256_file(junit) if junit.is_file() else None,
                  "counts": {s: sum(v == s for v in results.values()) for s in ("passed", "failed", "skipped")}},
        "register_sha256": sha256_file(REGISTER),
        "findings_sha256": sha256_file(FINDINGS) if FINDINGS.is_file() else None,
        **verdict,
    }
    path = out_dir / "preproduction_gate.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary = {"decision": report["decision"], "commit": commit,
               "gates": {k: v["status"] for k, v in report["gates"].items()},
               "accepted_capabilities": f"{report['accepted_capabilities']}/{report['total_capabilities']}",
               "tests": report["suite"]["counts"], "report": str(path.relative_to(ROOT)),
               "report_sha256": sha256_file(path)}
    print(json.dumps(summary, indent=2))
    return 0 if report["decision"] == "GO_FOR_SUPERVISED_PREPRODUCTION" else 1


if __name__ == "__main__":
    sys.exit(main())
