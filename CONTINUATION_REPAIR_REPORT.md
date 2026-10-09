# HOOD & X — Continued Remediation (2026-10-09)

## Release decision
**NO-GO for preproduction.** This is an incremental security and test-truthfulness repair, not verification of all HOOD features.

## Source basis
- Parent checkpoint: `4cdbc0a` (Sentinel health remediation)
- Branch: `fix/portable-boundaries-20261009`

## Implemented
1. **Cross-platform workspace validation:** rejects Windows drive paths, UNC paths and Windows separators on POSIX, as well as null bytes and empty paths. Realpath confinement still protects against symlink escape. Previously `C:\\Windows\\System32` on POSIX was treated as a relative directory and could produce confusing directory errors.
2. **Desktop input fail closed:** L2-L5 text typing and keyboard shortcuts refuse execution unless an action-bound approval mechanism is available. Current direct desktop methods do not accept such a token; high-risk operations consequently remain blocked rather than executing without approval. This is intentionally not a complete desktop approval workflow.
3. **Integration-test truthfulness:** older repository audit test no longer expects a consequential recommendation to complete without approval/checker evidence. It verifies two nonconsequential tasks complete while the consequential task is WAITING_APPROVAL, and that no fictitious checker receipt exists.
4. **New regression coverage:** foreign-platform paths, symlink escape, empty/null paths, permitted relative paths.

## Actual checks
- Focused portable-path, desktop and gateway tests: **15 passed**.
- Selected hardening, sentinel, auth, interaction, preproduction, release, unit, desktop and two integration suites: **117 passed, 1 deselected**, command:
  `python -m pytest -q tests/hardening tests/sentinel tests/auth tests/interaction tests/preproduction tests/release tests/unit tests/desktop tests/integration/test_real_tool_gateway.py tests/integration/test_real_orchestration.py -k 'not bootstrap_idempotent' --tb=short`
- Earlier offline diagnostic (without browser/dev-executor and two live internet suites): **208 passed, 11 failed**; this preceded the fixes in this batch, and does not represent a post-fix full suite.

## Known blockers (not fixed)
- Playwright Chromium navigation blocked with ERR_BLOCKED_BY_ADMINISTRATOR in this container.
- Windows-native desktop, screen/audio and firewall behavior needs physical Windows test hardware.
- Live Gemini, OpenAI and internet integration tests need explicit opt-in, configured test credentials, and controlled budget.
- `demo_app/app_service.py` deliberately contains a divide-by-zero defect used for the defect-reproduction exercise; its tests should be handled as a separate negative-control suite, not quietly changed or treated as a production regression.
- Legacy cinematic UI/telemetry tests include obsolete strings and requests without authenticated sessions; some endpoints correctly reject them.
- Bootstrap prerequisite checks remain blocked by missing host prerequisites.
- Full preproduction acceptance for 26 registered capabilities remains NO-GO.

## Next work items
1. Separate negative-control, live-provider, browser and host-native tests into explicitly labeled pytest profiles without hiding red checks.
2. Implement an action-bound approval mechanism for desktop typing/shortcuts instead of permanently blocking high-risk input.
3. Rework UI HTTP test fixtures to authenticate and to assert actual current behavior, not legacy status strings.
4. Run full end-to-end browser and Windows suites on the owner machine and attach reproducible evidence.
