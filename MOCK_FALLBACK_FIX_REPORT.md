# HOOD operational provider honesty — checkpoint 2026-10-09

## Change
- The model gateway no longer silently substitutes the test mock provider after live-provider failures in development/staging/production.
- Mock responses require either `preferred_provider=MOCK`, an explicit `allowed_providers` containing MOCK, or `EnvironmentProfile.TEST`.
- Operational failures now propagate as provider errors, and no fabricated completion is presented as a live AI result.
- Legacy resilience and fallback tests now expressly opt in to their mock provider. One stale acceptance test was corrected to require that unexecuted/failing mission steps do **not** become COMPLETED.
- Five new tests cover real-provider failure, explicit mock opt-in, explicit mock preference, test-profile behavior, and provider allowlist enforcement.

## Evidence
- Selected suite: **125 passed** (`tests/unit tests/hardening tests/auth tests/interaction tests/sentinel tests/acceptance tests/resilience --ignore=tests/unit/test_bootstrap.py`).
- Offline release gate command: PASS; reports NOT_PRODUCTION_READY.
- Preproduction gate: exit 1, NO_GO; 26 capabilities still lack acceptance evidence.

## Limitations
- Real Windows desktop/voice, browser navigation, and external AI provider availability have not been validated by this change.
- Simulated data must remain identified as simulated in higher-level UI and artifacts.
- Accounting, concurrency, wider autonomous work execution and full suite remain outside this batch's validation.
- No deployment, API calls, or external services were used.
