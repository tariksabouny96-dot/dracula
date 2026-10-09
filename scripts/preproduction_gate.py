"""Conservative HOOD preproduction acceptance gate.

Runs no live providers or external side effects. Writes machine-readable evidence.
It intentionally never treats source existence or stubbed unit tests as production proof.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'artifacts' / 'preproduction_acceptance.json'

# These domains need execution-level evidence before preproduction release.
LIVE_REQUIREMENTS = {
    'conversation': 'Authenticated end-to-end conversation, restart continuity and isolation',
    'orchestrator': 'Real concurrent agent work, independent proof and restart recovery',
    'governance': 'Actor-scoped approval, revocation and adversarial execution-boundary tests',
    'x': 'Windows owner confirmation, time-bound authority and forced deactivation',
    'emergency': 'Stop propagation to every active worker and child process',
    'sentinel': 'Windows host-based security observations and defensible evidence',
    'tool_gateway': 'Disposable-workspace tools with actual identity/approval enforcement',
    'development': 'Isolated real project build, rollback and artifact checks',
    'browser': 'Installed Chromium Playwright E2E with clean event-loop fixtures',
    'desktop': 'Local Windows desktop keyboard/mouse verification with consent',
    'memory': 'Restart, tenant isolation, retention and tamper tests',
    'audit': 'Log completeness and integrity under restarts and failures',
    'gemini': 'Opted-in live provider validation, quota and actual billing evidence',
    'openai': 'Opted-in live provider validation, quota and actual billing evidence',
    'local_llm': 'Loopback inference server and real model output verification',
    'hood_model': 'Actual served inference model and independently tested quality',
    'voice': 'Real microphone, STT/TTS and accessibility acceptance',
    'ecommerce': 'Sandbox storefront, order lifecycle and idempotency tests',
    'freelance': 'Sandbox opportunity-to-delivery process and artifact validation',
    'marketing': 'Authorized test-only mail and consent/unsubscribe checks',
    'calendar': 'Sandbox calendar auth and event lifecycle',
    'email': 'Sandbox mailbox auth and send/draft lifecycle',
    'artifacts': 'PDF/XLSX creation, file integrity and browser download',
    'nodes': 'Real two-node signed, replay-protected communications',
    'economics': 'Measured token/billing reconciliation, budget preflight and spend stop',
    'evolution': 'Reproducible evaluation, promotion gate and rollback',
}


def command_check(name, cmd, timeout):
    try:
        run = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                             timeout=timeout, check=False, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        return {'name': name, 'status': 'PASS' if run.returncode == 0 else 'FAIL',
                'exit_code': run.returncode, 'command': cmd,
                'output_tail': (run.stdout + run.stderr)[-5000:]}
    except subprocess.TimeoutExpired:
        return {'name': name, 'status': 'BLOCKED', 'reason': 'Timed out', 'command': cmd}
    except OSError as exc:
        return {'name': name, 'status': 'BLOCKED', 'reason': type(exc).__name__, 'command': cmd}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full-suite', action='store_true', help='Run offline full suite (may fail due to missing prerequisites)')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from services.capabilities.registry import get_capability_inventory
    inventory = get_capability_inventory()
    checks = [command_check('selected_offline_regressions',
        [sys.executable, '-m', 'pytest', '-q', 'tests/hardening', 'tests/unit', 'tests/release',
         'tests/preproduction', 'tests/simulation', '--ignore=tests/unit/test_bootstrap.py'], args.timeout),
        command_check('python_compile', [sys.executable, '-m', 'compileall', '-q',
                      'services', 'packages', 'ui', 'hood_cli.py'], args.timeout)]
    checks.append(command_check('controlled_simulation_contract',
        [sys.executable, '-m', 'pytest', '-q', 'tests/simulation'], args.timeout))
    if args.full_suite:
        checks.append(command_check('full_suite',
            [sys.executable, '-m', 'pytest', '-q', '--disable-warnings'], args.timeout))
    else:
        checks.append({'name':'full_suite', 'status':'NOT_RUN', 'reason':'Use --full-suite to run explicitly'})
    capabilities = []
    for item in inventory['items']:
        cap = dict(item)
        cap['acceptance_requirement'] = LIVE_REQUIREMENTS.get(item['id'], 'Independent end-to-end proof')
        cap['acceptance_status'] = 'NOT_VERIFIED'
        capabilities.append(cap)
    no_go = [f"{cap['id']}: {cap['acceptance_requirement']}" for cap in capabilities]
    no_go += [f"{c['name']}: {c['status']}" for c in checks if c['status'] != 'PASS']
    no_go += ['Target-machine Windows validation not performed',
              'No staging deployment target, secrets handling and rollback verified',
              'No live provider cost/rate-limit validation performed',
              'Security review by independent auditor outstanding']
    evidence = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'source_git_commit': subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True).stdout.strip(),
        'host_platform': platform.platform(),
        'decision': 'NO_GO', 'scope': 'offline source and selected tests; no deployment',
        'verified_capabilities': 0, 'total_capabilities': len(capabilities),
        'capabilities': capabilities, 'checks': checks,
        'release_blockers': no_go,
        'interpretation': 'A source module, stubbed test, or a passing unit suite is not production acceptance evidence.'
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps({'decision':evidence['decision'], 'checks': {c['name']:c['status'] for c in checks},
                      'capabilities_pending':len(capabilities), 'report':str(OUTPUT)}, indent=2))
    return 1  # A fail-closed release gate exits nonzero when acceptance has not been established.


if __name__ == '__main__':
    sys.exit(main())
