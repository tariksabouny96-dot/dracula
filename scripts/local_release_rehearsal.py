"""Local release rehearsal: clean export -> boot -> smoke -> stop -> backup -> restore -> reboot.

This is NOT staging evidence (gate G6 needs an isolated staging host). It proves
the exported commit installs and boots from scratch with a fresh data directory,
that the security boundaries answer correctly over real HTTP, and that a data
backup restores into a working instance. Writes release/staging/LOCAL_REHEARSAL.json.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tarfile
import io
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release" / "staging" / "LOCAL_REHEARSAL.json"
spec = importlib.util.spec_from_file_location("hood_backup", ROOT / "scripts" / "hood_backup.py")
hood_backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hood_backup)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, path, data=None, cookie=None, csrf=None, host=None):
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if cookie:
        headers["Cookie"] = cookie
    if csrf:
        headers["X-CSRF-Token"] = csrf
    if host:
        headers["Host"] = host
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def boot(src: Path, data: Path, port: int):
    env = {**os.environ, "HOOD_DATA_DIR": str(data), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.Popen([sys.executable, "hood_cli.py", "ui", "--port", str(port)], cwd=src, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if call(base, "/api/auth/status")[0] == 200:
                return proc, base
        except OSError:
            pass
        time.sleep(0.2)
    proc.kill()
    raise RuntimeError("Server did not start: " + proc.stdout.read().decode(errors="replace")[-2000:])


def main() -> int:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    checks = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src, data = tmp / "src", tmp / "data"
        src.mkdir()
        archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
        tarfile.open(fileobj=io.BytesIO(archive)).extractall(src, filter="data")
        checks["clean_export"] = "PASS"

        proc, base = boot(src, data, free_port())
        try:
            checks["boot"] = "PASS"
            checks["static_index"] = "PASS" if call(base, "/")[0] == 200 else "FAIL"
            checks["rebinding_refused"] = "PASS" if call(base, "/api/auth/status", host="evil.example")[0] == 421 else "FAIL"
            checks["unauthenticated_api_refused"] = "PASS" if call(base, "/api/telemetry")[0] == 503 else "FAIL"
            ok = call(base, "/api/auth/init", {"username": "owner", "display_name": "Owner",
                                               "password": "RehearsalPassword123!"})[0] == 200
            checks["owner_setup"] = "PASS" if ok else "FAIL"
            status, body, headers = call(base, "/api/auth/login", {"username": "owner", "password": "RehearsalPassword123!"})
            cookie = headers.get("Set-Cookie", "").split(";")[0]
            csrf = json.loads(body).get("csrf_token") if status == 200 else None
            checks["login"] = "PASS" if status == 200 and cookie else "FAIL"
            checks["csrf_enforced"] = "PASS" if call(base, "/api/chat", {"text": "hi"}, cookie)[0] == 403 else "FAIL"
            checks["chat"] = "PASS" if call(base, "/api/chat", {"text": "hello"}, cookie, csrf)[0] == 200 else "FAIL"
            status, body, _ = call(base, "/api/agents/missions", {"objective": "Build a tiny notes web app with CRUD",
                                                                  "budget_usd": 0.5, "confirm": True}, cookie, csrf)
            state = json.loads(body).get("state") if status == 201 else None
            # Without an approved, priced provider the planner must refuse rather than fabricate a plan.
            checks["agent_mission_fails_closed_without_provider"] = "PASS" if state == "BLOCKED" else f"FAIL ({status} {state})"
            stop = call(base, "/api/emergency_stop", {})
            checks["emergency_stop"] = "PASS" if stop[0] == 200 and json.loads(stop[1])["latch_engaged"] else "FAIL"
            refused = call(base, "/api/agents/missions", {"objective": "another objective here", "confirm": True}, cookie, csrf)
            checks["stop_blocks_new_work"] = "PASS" if refused[0] == 423 else f"FAIL ({refused[0]})"
            checks["stop_reset"] = "PASS" if call(base, "/api/emergency_stop/reset", {"confirm": True}, cookie, csrf)[0] == 200 else "FAIL"
        finally:
            proc.terminate()
            proc.wait(timeout=20)

        backup = hood_backup.backup(data, tmp / "backups", include_keys=True)
        hood_backup.verify(backup)
        restored = tmp / "restored"
        hood_backup.restore(backup, restored)
        checks["backup_verify_restore"] = "PASS"
        proc, base = boot(src, restored, free_port())
        try:
            status, body, _ = call(base, "/api/auth/login", {"username": "owner", "password": "RehearsalPassword123!"})
            checks["restored_instance_login"] = "PASS" if status == 200 else "FAIL"
            cookie = _.get("Set-Cookie", "").split(";")[0] if status == 200 else None
            hist = json.loads(call(base, "/api/chat/history", cookie=cookie)[1]) if cookie else {}
            checks["restored_chat_history"] = "PASS" if any(m["text"] == "hello" for m in hist.get("messages", [])) else "FAIL"
        finally:
            proc.terminate()
            proc.wait(timeout=20)

    result = {"kind": "LOCAL_REHEARSAL_NOT_STAGING", "commit": commit,
              "generated_at": datetime.now(timezone.utc).isoformat(), "host": sys.platform,
              "checks": checks, "overall": "PASS" if all(v == "PASS" for v in checks.values()) else "FAIL",
              "not_covered": ["isolated staging host", "TLS/reverse proxy", "Windows target", "rollback to a previous release",
                              "live provider credentials"]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
