"""Offline release evidence gate: produces honest, reproducible local checks.

This intentionally never deploys, contacts APIs, scans machines or reads secrets.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    ('hardening_and_unit', [sys.executable, '-m', 'pytest', '-q', 'tests/hardening', 'tests/unit', 'tests/release', '--ignore=tests/unit/test_bootstrap.py']),
    ('python_syntax', [sys.executable, '-m', 'compileall', '-q', 'services', 'packages', 'ui', 'hood_cli.py']),
]

def main():
    results = {}
    for label, command in CHECKS:
        try:
            proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
            results[label] = {'status': 'PASS' if proc.returncode == 0 else 'FAIL', 'exit_code': proc.returncode, 'output_tail': (proc.stdout + proc.stderr)[-2500:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[label] = {'status': 'BLOCKED', 'reason': type(exc).__name__}
    results.update({
        'windows_desktop': {'status': 'NOT_VERIFIED', 'reason': 'Requires owner Windows workstation'},
        'live_model_providers': {'status': 'NOT_VERIFIED', 'reason': 'Would require external API calls and billing consent'},
        'full_browser_e2e': {'status': 'NOT_VERIFIED', 'reason': 'Browser/runtime validation not part of offline release gate'},
        'deployment': {'status': 'NOT_PERFORMED', 'reason': 'Explicit target, credentials and deployment authorization not supplied'},
    })
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'release_readiness': 'NOT_PRODUCTION_READY',
        'results': results,
    }
    target = ROOT / 'artifacts' / 'offline_release_gate.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'release_readiness': report['release_readiness'], 'checks': {k:v['status'] for k,v in results.items()}}, indent=2))
    return int(any(v['status']=='FAIL' for v in results.values()))

if __name__ == '__main__':
    raise SystemExit(main())
