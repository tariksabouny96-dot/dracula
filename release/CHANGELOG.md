# Change log — branch `claude/hello-mecuky`

| Commit | Batch | Summary |
|--------|-------|---------|
| `faafb93` | 0 | Imported upstream checkpoint `33c364a` unmodified (baseline 238 pass / 9 fail / 17 error). |
| `c46800d` | 1 | P0 boundaries: Host allowlist, CSRF, CSP, hashed sessions, persistent stop latch at the tool gateway, honest stop report, approval TTL + principal binding + DENY audit, atomic budget reservations and unknown-price refusal, X authority by role, UI escaping. |
| `4d8359e` | 2 | `services/agents`: validated planner, durable leased DAG, role-scoped network-isolated sandbox, deterministic verifier, repair loop, HMAC receipts, hashed artifacts; vault env key + permission check + rotation. |
| `3d9d71c` | 3 | Agent engine wired into HTTP API, CLI and UI; UI truthfulness fixes; mobile emergency-stop overflow fix; real-browser tests and screenshots. |
| `28d388c` | 4a | Every failing test root-caused; live tests made explicit `BLOCKED_EXTERNAL` profiles. 331 pass / 0 fail. |
| `056fa4f` | P1-01 | Durable per-principal conversation history with erase; requirements.txt to UTF-8. |
| `405806e` | 4b | Evidence-consuming gate, F01–F36 ledger, 26-capability register, backup/restore, release rehearsal, CI, SBOM, docs. |
| `0a9dd17` | 4b | Register correction (voice). Gated commit: NO_GO, 344 pass / 0 fail / 6 skipped. |

Breaking changes: session tokens are now stored as digests (all existing sessions are invalidated);
cookie-authenticated POSTs require `X-CSRF-Token`; `reset_stop()` requires `is_root_owner=True`;
approvals expire (default 15 min); paid model calls require a pricing file.

## Follow-up — branch `claude/eager-dirac-xxzqst` (continues from `claude/hello-mecuky`)

| Area | Summary |
|------|---------|
| Tests | Restored the offline suite to green (two stale UI tests followed the classic console to `/classic`). |
| F25 (P0) | Principal-scoped memory (new `principal` dimension, isolated queries) and an enforced trust-promotion ladder (evidence required, forward-only, owner-only ESTABLISHED). |
| F26 (P1) | `query_semantic` renamed `query_lexical` (honest); `PostgreSQLBackend` refuses to pose as a live DB. |
| F09 (P0) | Legacy domain leads emit only explicitly UNVERIFIED model reasoning — no hardcoded PASSED/VALIDATED/OPTIMAL verdicts, no fabricated evidence, mock output labelled. |
| F12 (P0) | Executive X state is durably persisted and restored; an expired session fails closed on restart; consumed approval ids survive (no cross-restart replay). |
| F16 (P1) | X activation reports the duration actually granted and tells the user when a request was clamped. |
| F27 (P0) | Node migration bundles carry an HMAC sender signature (fail closed without a valid one) and import transactionally. |
| G2 | Live-provider golden journey run end-to-end on free-tier Gemini via the environment proxy — COMPLETED, verifier PASS, receipts valid, $0. Evidence under `release/evidence/live/`. |

Offline suite after this work: 390 passed, 0 failed, 6 live-only deselected.

### Egress firewall + exports (branch `claude/eager-dirac-xxzqst`, continued)

| Area | Summary |
|------|---------|
| F21 | Document export pipeline (md/html/pdf/docx/xlsx/csv/zip) with independent validators, formula-injection defence, and an owner-scoped artifact registry (write-once files, single-use HMAC download tokens, cross-tenant isolation). |
| Firewall | New `services/firewall`: default-deny egress policy, owner-only rule management, fail-closed, emergency-stop-aware, audited, enforced in the model gateway before any live provider call. Verified against real Gemini (blocked by default; allowed only when the owner permits the host). |
| CI | pytest-timeout + job ceilings; Windows socket-timeout guard; docker-build job verifies the container image. |
| Startup | Fixed "it doesn't work" setup failures found by a fresh-clone run: a missing Playwright no longer crashes all of Hood (browser automation just turns off); missing required packages give the exact install command instead of a traceback; `hood_cli.py` now loads `.env` (env.example always told users to create one, but nothing read it, so keys put there were ignored); `status` reports `KEY_SET_BUT_NO_PRICING` instead of a misleading state; examples no longer point at the retired `gemini-2.5-flash` (HTTP 404) and ship `HOOD_MODEL_PRICING`; owner-setup curl commands send the JSON header the server requires; `.env` is excluded from the Docker build context so a key there can never be baked into the image; RUNBOOK gains "Connect the model" and a troubleshooting table. Verified end to end: fresh copy → `.env` → owner setup → login → live Gemini chat reply. |
| Owner Windows test | Fixes from the owner's first Windows run, plus model setup in the UI. **Settings › Model provider**: owner pastes/replaces/removes the Gemini key (encrypted vault, applied without restart, never echoed back), chooses free-tier or custom prices, and runs a one-call connection test. **Voice is now real**: browser recording → Gemini speech-to-text, Gemini text-to-speech → WAV playback, governed by key, firewall, price, spend caps, emergency stop, and recorded consent before any microphone audio is sent (verified end to end against live Gemini). **Exports self-test failed on Windows** (ArtifactError): the artifact registry treated Windows' missing POSIX permission bits as "world-readable", lacked `O_NOFOLLOW`, and opened files in text mode (would corrupt PDF/DOCX/ZIP); fixed with explicit symlink refusal and binary mode. **Provider badge** now reflects what this run actually observed (chat and missions), not stale failures from before a key was set; new states `AI READY` / `AI PRICING NOT SET`. **Capability cards** show live observed state instead of "unknown"; firewall, self-learning and self-development added to the inventory and audit register. **Agents page** layout (nested grids) fixed. **WinError 10053 tracebacks** from browser disconnects no longer flood the console. Clearer unknown-price mission error; Commerce says "not built yet"; record button's stop label no longer looks like the emergency STOP. |
