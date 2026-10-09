# HOOD NOVA 2.3 — Governed Local Task Graph

**Status:** controlled local development build. This release does NOT implement autonomous multi-agent work or completion of users' business objectives.

## Implemented
- Added explicit second-stage owner-only `POST /api/operations/execute` confirmation, separate from the first approval that creates the Markdown plan.
- Executes three real deterministic, bounded local steps (input check, scope boundary record, evidence manifest), subject to a validated DAG dependency order. This is local code execution, NOT an LLM or delegated agent action.
- Writes a single generated JSON execution manifest to the mission artifacts directory using exclusive file creation. It includes step results and SHA-256 output hashes, with a final verified integrity receipt persisted in SQLite.
- The generated manifest explicitly states `objective_completed: false`, `agent_execution: false`, and `external_actions: false`.
- Enforces owner scoping, one-time execution, previously verified approved plan, non-overwrite, and integrity checks on owner-only `GET /api/operations/execution-artifact/<mission_id>`.
- Missions UI can authorize this limited second stage, show the result, and download its verified JSON manifest. All user-controlled text continues to use safe text rendering.

## Tests performed
- `python -m pytest -q tests/hardening/test_nova23_execution.py tests/hardening/test_nova22_chat_missions.py tests/hardening/test_nova21_missions.py tests/hardening/test_nova_registry.py tests/unit tests/auth tests/interaction tests/adversarial -k 'not bootstrap_idempotent'`: **70 passed, 1 deselected**.
- `node --check ui/static/app.js`: passed.
- `python -m compileall -q services/operations ui/server.py`: passed.
- No external service, model, browser, X, or OS desktop-control execution performed.

## Remaining limits
- No user-configurable task DAG, autonomous agent execution, background scheduler, pause/resume or active cancellation. The 3 steps run synchronously under a process-local lock.
- A SHA-256 receipt verifies bytes, not business outcomes or independent security attestation.
- Database/filesystem atomicity across abrupt process loss and concurrent separate server processes is not guaranteed.
- Full suite, authenticated UI screenshots, Windows hardware, and browser-e2e tests are not verified by this selected test run.
- This build must not be used for unattended business operations.
