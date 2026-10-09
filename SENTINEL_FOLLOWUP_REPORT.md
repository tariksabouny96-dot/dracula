# HOOD full-suite follow-up: Sentinel evidence integrity

Date: 2026-10-09
Base: 7d5c53e (full-suite repair checkpoint)
Branch: fix/sentinel-evidence-20261009

## Implemented
- FirewallManager no longer constructs an artificial healthy profile when `netsh` fails, returns a nonzero exit status, or produces unrecognized output. Unverified firewall state fails closed.
- SecuritySentinelService displays `UNVERIFIED FIREWALL STATE` instead of a healthy posture when firewall telemetry is not verified.
- SelfHealingEngine no longer records blocked/out-of-scope cache deletion as executed; the requested target remains intact.
- Updated three legacy Sentinel assertions to test security-preserving semantics instead of hardcoded positive success.
- Added four evidence-integrity regression tests.

## Verification
- Sentinel focused: `python -m pytest -q tests/sentinel/test_evidence_boundaries.py tests/sentinel/test_sentinel_service.py` -> 10 passed.
- Selected suite: `python -m pytest -q tests/sentinel tests/hardening tests/auth tests/interaction tests/preproduction tests/release tests/unit -k 'not bootstrap_idempotent'` -> 101 passed, 1 deselected.
- Attempted unrestricted suite: `python -m pytest -q --tb=line` -> timed out at 140s after approximately 59%; failures appeared before timeout. No full-suite pass is claimed. See `/mnt/data/hood_followup_full_suite.log` if working in this environment.

## Remaining blockers
- Real-browser navigation is blocked by this execution environment; do not count it as passed.
- Windows host tests require Windows hardware/OS.
- Live provider and internet tests require explicit credentials/network and should run in a separate opt-in environment.
- Remaining failing legacy behavior assertions need case-by-case review, without relaxing approvals, verification or identity checks.
- Preproduction release remains NO-GO.
