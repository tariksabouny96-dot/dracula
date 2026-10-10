# HOOD NOVA 2.5 — Opt-in LLM Specialist Reviews

## What changed

- Added `services/operations/llm_specialists.py`, which invokes the configured Gemini provider for two independently prompted advisory roles: delivery architect and risk analyst.
- Model responses require an exact JSON schema, bounded field lengths, and a real (`is_mock=False`) provider response. They are **not fact checked** and cannot authorize actions.
- Added owner-scoped durable review receipts and tamper-checked JSON artifacts in SQLite and the mission workspace.
- Added owner-only POST `/api/operations/llm-analyze` and GET `/api/operations/llm-artifact/<mission-id>`.
- Added a real UI action and artifact link in Missions; kept the deterministic specialist path separately available.
- Restored `packages/auth/vault.py` omitted from the inherited 2.4 source ZIP (from the verified earlier baseline).

## How to enable

1. Install requirements in an isolated environment and configure the Gemini API key via `GEMINI_API_KEY` or existing HOOD vault mechanism.
2. Explicitly set environment variable `HOOD_ENABLE_LLM_SPECIALISTS=1` before starting Hood. The feature is otherwise disabled.
3. Log in as the privileged local owner, create a mission and approve its local planning artifact.
4. In Missions, select **Run Gemini AI specialist review (opt-in)** and confirm provider network access.
5. Review or download the schema-validated JSON report. It contains two advisory analyses but **no claim of business execution**.

## Limitations

- No live Gemini API requests were made in this environment. Tests use injected provider stubs.
- Provider use may consume paid API quota and incur costs. No guarantee of free tier, and no hard budget/reservation mechanism for this new path.
- This is not durable background agent scheduling, tool calling, autonomous execution, or independent factual verification. The two roles use the same provider family.
- If either role fails, the invocation is not stored as a completed review. An earlier provider call may still have consumed quota.
- Existing hardening and unit tests include a bootstrap test that fails in this environment because of runtime prerequisites. Do not interpret the selectively passing suite as full Windows acceptance.
- Full browser UI automation, voice, and Windows-specific functionality remain unverified.

## Safety invariants

- Feature disabled by default via environment gate.
- Explicit per-request user confirmation and acknowledgement of network/usage costs.
- Owner authorization enforced on both initiation and report download.
- Approved local plan required; one-time completed-review receipt.
- Reports marked advisory and `objective_completed=false`.
- No shell, browser, file modification, or other tools are made available to the LLM roles.
