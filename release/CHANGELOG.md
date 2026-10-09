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
