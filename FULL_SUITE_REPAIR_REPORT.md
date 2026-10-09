# HOOD full-suite repair checkpoint — 2026-10-09

## Scope
Baseline: preproduction checkpoint `6cbeff3`, previously 199 passed, 17 failed, 17 setup errors. The changes in this branch do **not** resolve every failing test and do not justify preproduction approval.

## Fixes
1. Restored `demo_app/app_service.py`, `demo_app/server.py`, and `demo_app/tests/test_app.py` from the original user-supplied Hood archive. The preproduction source package had excluded this deliberately broken demo fixture, causing stack-detection and executor tests to fail for packaging rather than product reasons.
2. Updated Playwright's browser bootstrap to accept `HOOD_CHROMIUM_EXECUTABLE` or installed system Chromium/Chromium Browser, with cleanup on failed launch. This fixes the missing Playwright-managed Chromium executable when a system browser is present.

## Verified
- Focused fixture, stack, and browser-start tests plus hardening: 38 passed.
- Hardening/auth/interaction/preproduction/release/unit selected suite: 91 passed, with bootstrap environmental test explicitly outside scope.
- Browser and dev-executor combined suite: 8 passed, 10 failed. All 10 failures trace to Chromium refusing local/external navigation with `ERR_BLOCKED_BY_ADMINISTRATOR` in this runner; there is no claim that browser navigation passed.
- Full suite rerun was attempted and interrupted by its execution timeout before completion; its partial output is not a meaningful suite result.

## Outstanding failures and blockers
- Live Gemini and external HTTP retrieval require permitted network access and explicitly configured test credentials. They must not be replaced with mock assertions or fabricated cost verification.
- Windows-native desktop, firewall, and process tests must be exercised on a Windows test machine; Linux negative results cannot certify Win32 behavior.
- Legacy tests expecting unapproved actions to execute, unauthenticated telemetry, fixed branding strings or fake self-healing success must be updated to assert current secure semantics after verifying the corresponding requirements.
- Headless Chromium launches here, but the environment's administrator policy blocks navigation; run browser E2E in an unrestricted disposable local test profile, without contacting production services.
- Validate full suite again with an explicit integration-test matrix, platform guards, safe test credentials and bounded execution time.

## Readiness
NO-GO. The original set of 17 failures/17 errors has not been completely repaired. The reported successes are limited to explicitly executed subsets. No deployment attempted.
