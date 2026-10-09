# HOOD NOVA 2.2 — Chat-to-Mission Handoff

## Scope and readiness
Controlled local development release. **No autonomous execution has been implemented or claimed.** This increment makes planning missions usable directly from the chat console and attaches persistent reviewable stage records to every mission.

## Implemented
- Explicit owner-only `/mission <objective>` command in `POST /api/chat`, authenticated via existing HTTP session and permissions. It creates a mission directly in the NOVA SQLite store, then returns an accurate pending-state message. It does not invoke LLMs, shell commands, browser automation, agents, or external services.
- Four persisted mission stage records: REVIEW, APPROVAL, ARTIFACT, VERIFICATION. Stage status accurately begins pending/blocked, becomes cancelled on cancellation, and advances only on approved local Markdown artifact creation.
- Stage receipts and SHA-256 evidence for locally generated artifact bytes. Evidence verifies the artifact only; it does not prove business-task completion or independent maker-checker validation.
- Mission UI displays persisted steps and evidence with `textContent`, preserving safe rendering.
- Chat placeholder and suggestion chip now expose the explicit command without implying autonomous task execution.

## Security boundaries
- Owner-only chat and mission APIs inherited from NOVA 2.1; no new privileged endpoint.
- Explicit local plan creation approval is still a separate API action and cannot be inferred from conversational language.
- The mission artifact path is generated internally, not supplied by a caller; downloads still verify owner identity and file SHA-256.
- All steps remain within local SQLite and the local controlled artifact directory.

## Verification
- `python -m pytest -q tests/hardening/test_nova22_chat_missions.py tests/hardening/test_nova21_missions.py tests/hardening/test_nova_registry.py tests/unit tests/auth tests/interaction tests/adversarial -k 'not bootstrap_idempotent'`: 67 passed, 1 deselected.
- `node --check ui/static/app.js`: passed.
- `python -m compileall -q services/operations ui/server.py`: passed.
- No live APIs, browsers, production services, X activation, or external commands invoked by the new workflow.

## Known limitations
- This is not a generalized model tool-calling orchestrator. Ordinary chat text is unchanged; **only** explicit `/mission` commands are routed to the persistent mission service.
- Stage records are a durable *planning checklist*, not a parallel DAG scheduler or agent execution records.
- Stage 1 REVIEW records acceptance of user input, not independent objective verification.
- Database/filesystem atomicity across hard crashes, SQLite multi-process coordination, authenticated end-to-end browser visual QA, Windows desktop and audio remain unverified.
- Existing application and historical tests may have separate unresolved failures not covered by this selected suite.

## Local validation
1. Extract into a new directory and preserve the existing Hood install as backup.
2. Run the test command above inside a development virtual environment.
3. Start Hood in its local authenticated mode, log in as owner and enter `/mission Draft a fake client website plan with responsive pages and QA` in chat.
4. Open Missions and verify the new mission has four stage statuses and no receipt.
5. Approve local artifact generation with explicit confirmation, then download the Markdown file and check its SHA-256 receipt.
6. Restart Hood; verify persistence, cancellation of a new pending mission, and rejection of viewer access.
