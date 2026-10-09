# HOOD & X — provider runtime engineering checkpoint (2026-10-09)

## Readiness: NOT production-ready; NOT deployed
Owner authorizes continued engineering, but the requested complete, autonomous and fully tested product **has not** been demonstrated. This checkpoint does not supersede the audit finding ledger in `REMEDIATION_REPORT.md`.

## Implemented in this checkpoint
- A genuine OpenAI Responses API text adapter, disabled by default and gated on `HOOD_ALLOW_OPENAI_API_CALLS=1`, explicit enabled provider configuration and a configured `OPENAI_API_KEY` or vault secret. The adapter sends no requests during offline tests. No model tool calls are exposed.
- Model selection per `HOOD_OPENAI_FAST_MODEL`, `HOOD_OPENAI_STANDARD_MODEL`, `HOOD_OPENAI_DEEP_MODEL`, with model-name validation, response-length cap, completion-state check, explicit nonempty output, sanitized HTTP error classification, and provider-token parsing. The cost field remains an **unknown estimate of 0** for contract compatibility, not a billing receipt.
- Loopback-only HTTP local-model endpoints, no proxy forwarding and no redirect following; system and user prompts correctly transmitted, size and response validation, no fabricated token usage, error propagation.
- Zero cost estimates are no longer called verified free-tier requests in the cost ledger.
- Added offline model transport contract tests, updated a local transport stub to use the hardened opener API.

## Tests run here
- `python -m pytest -q tests/hardening tests/unit tests/release --ignore=tests/unit/test_bootstrap.py`: **66 passed**.
- `python -m compileall -q services packages ui hood_cli.py`: passed.
- Full `python -m pytest -q --disable-warnings`: **195 passed, 17 failed, 17 errors**. Causes include absent live Gemini credentials, blocked internet, Windows-only desktop assumptions, obsolete behavioral assertions, missing fixture/demo environment, and Playwright sync API under an async event loop. Full log outside source archive: `/mnt/data/hood_full_final_attempt.log`.

## Critical unfinished items
- Full P0/P1 audit finding remediation and independent security review.
- Reliable mission-driven multi-agent planning, controlled real tool execution, independently established postconditions, durable cancellation and recovery.
- Actual live OpenAI/Gemini billing, rate-limit and quota verification with a user-approved spending ceiling. Existing token-ledger budgets do **not** guarantee no charges on external APIs.
- Authenticated Windows app E2E, browser tests with compatible Playwright fixtures, native desktop/microphone tests and complete UI mockup acceptance.
- Full external business integrations, e-commerce fulfillment, development project delivery, network actions, and X controls on target machine.
- Staging infrastructure, secrets provisioning, monitoring, backup and rollback, threat model, explicit deployment target and production deployment.

## Deployment decision
**NO-GO.** Do not expose the service publicly or authorize unattended customer/client work. Developer preview only. Never infer provider free-tier eligibility from `estimated_cost_usd == 0.0`.
