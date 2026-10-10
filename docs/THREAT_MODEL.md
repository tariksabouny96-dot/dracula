# Threat model (agent engine, HTTP surface, X)

Scope: single-host, loopback-bound Hood instance operated by one Root Owner with optional additional
accounts. Method: STRIDE per trust boundary (see `architecture/ACTUAL_SYSTEM_MAP.md`). Status column
reflects this branch; "test" names a regression test in `tests/`.

| Threat | Boundary | Mitigation | Status / evidence |
|--------|----------|------------|-------------------|
| Malicious web page drives the local API (CSRF) | T1 | CSRF token on cookie POSTs; SameSite=Strict; JSON-only | Mitigated — `security/test_p0_boundaries.py::test_csrf_required_for_cookie_sessions_not_for_bearer` |
| DNS rebinding hijacks first-run setup | T1 | Host header allowlist (loopback names + `HOOD_ALLOWED_HOSTS`) | Mitigated — `::test_dns_rebinding_host_is_refused` |
| Internet client claims the Root Owner through the reverse proxy (Caddy reaches HOOD over loopback) | T1 | Setup only for direct-local requests (any proxy header = remote) **and** the one-time setup code printed at start; atomic owner creation; `hood_cli.py init-owner` for servers | Mitigated — `security/test_proxy_owner_boundaries.py::test_nobody_can_claim_the_root_owner_through_the_proxy`, `::test_two_simultaneous_first_runs_create_one_owner` |
| Stranger's wrong passwords lock the owner out (all proxied clients shared the proxy's 127.0.0.1 rate-limit bucket) | T1 | Client address from the trusted proxy's `X-Forwarded-For` (rightmost, Caddy overwrites it); untrusted forwarded headers never believed | Mitigated — `::test_a_strangers_wrong_passwords_do_not_lock_the_owner_out`, `::test_a_client_cannot_choose_its_own_rate_limit_bucket` |
| Documented `HOOD_ALLOWED_HOSTS=name:443` refused every proxied request (Host has no port) | T1 | Default port optional in Host/Origin matching | Mitigated — `::test_public_host_works_through_the_proxy_and_others_are_refused` |
| Anonymous emergency stop from the internet (denial of service) | T1 | Unauthenticated stop only for direct-local requests; remote needs a session | Mitigated — `::test_emergency_stop_needs_a_session_when_it_comes_through_the_proxy` |
| Parallel guesses slip past the login limit (check and count were separate) | T1 | Each attempt counted atomically before the password is checked | Mitigated — `security/test_review_fixes_batch1.py::test_parallel_wrong_passwords_cannot_exceed_the_limit` |
| Distributed password guessing against the owner from many addresses / one IPv6 /64 | T1 | Per-address limits (IPv6 per /64), plus 30 remote attempts per username per 15 min across all addresses (never applied at the machine itself); strong-password rule + scrypt | Mitigated for volume — `::test_ipv6_addresses_in_one_slash64_share_a_bucket`, `::test_distributed_remote_guessing_is_capped_but_the_owner_at_the_machine_still_gets_in`; **2FA/passkeys still recommended before public exposure** |
| Undeclared proxy (cloudflared, tailscale serve) or CDN collapses clients into the owner's local bucket | T1 | Forwarded requests from an untrusted proxy get their own bucket; behind a CDN set Caddy `trusted_proxies` to the CDN ranges | Mitigated (local) — `::test_an_undeclared_proxy_never_shares_the_owners_local_bucket`; CDN: configuration |
| Build machine's data (vault key, old stores, trusted-node registry) shipped in the image and imported on first start | T9 | `.dockerignore` excludes `artifacts/`, key files, logs; containers never run the legacy migration | Mitigated — `::test_build_machine_data_never_enters_the_image`, CI container checks |
| Migration follows links / imports someone else's files from the start folder | T9 | Links skipped, POSIX ownership checked, only the HOOD code folder is a source | Mitigated — `preproduction/test_data_paths_and_backup.py::test_old_stores_are_copied_once_and_never_overwrite` |
| Anonymous emergency stop by another local process (no proxy headers) | T1 | By design: stopping is the safe direction and needs local access; remote stop needs a session | **Accepted** (denial of service only) |
| Script injection via window titles, findings, user fields | T1 | textContent / escapeHtml; CSP `script-src 'self'` (no eval/inline) | Mitigated — browser test fails on CSP/console errors |
| Stolen auth DB → session hijack | T2 | Only SHA-256 digests of tokens stored | Mitigated — `::test_session_tokens_are_not_stored_in_plaintext` |
| Username enumeration by timing | T2 | Dummy scrypt on unknown users | Mitigated — `::test_unknown_user_login_costs_a_password_hash` |
| Non-root account named "zak" activates X | T8 | Authority from ROOT_OWNER role | Mitigated — `x_control/...::test_x_authority_comes_from_root_role_not_username` |
| Cross-tenant mission access | T2 | `owner` predicate on every engine query; 404 for absent and foreign | Mitigated — `agents/test_agent_http.py` |
| Prompt injection in objective makes agents escape | T3/T4 | Rules enforced in sandbox, not prompt; role write roots | Mitigated — `agents/...::test_prompt_injected_writes_are_blocked_at_the_boundary` |
| Model returns extra fields / tools / cyclic plan | T3 | `extra=forbid`, DAG validation | Mitigated — `::test_unsafe_or_malformed_plans_are_rejected` |
| Engineer forges QA tests to pass | T4 | Engineer cannot write `qa_tests/`; QA does not see engineer code | Mitigated — `::test_engineer_cannot_write_outside_its_area` |
| Test code rewrites the code under test | T5 | Workspace digest before/after verification ⇒ UNVERIFIED | Mitigated — `::test_verifier_flags_tests_that_rewrite_the_code` |
| Generated code exfiltrates over network | T5 | Network namespace with only loopback | Mitigated on Linux — `::test_sandbox_has_loopback_but_no_external_network` |
| Generated code reads host secrets from disk | T5 | Scrubbed env only | **Open** — needs container/VM or mount namespace; do not run on hosts with real data |
| In-process subversion of QA tests by app code (monkeypatching pytest) | T5 | none beyond role separation | **Open** — mitigation: out-of-process acceptance probes |
| Runaway spend via parallel calls | T6 | Atomic reservations; durable mission ledger | Mitigated — `::test_parallel_reservations_cannot_overspend` |
| Unknown or missing usage recorded as $0 | T6 | Unknown price refused; missing usage charged at reservation | Mitigated — `::test_unknown_price_refuses_paid_call`, `::test_missing_usage_is_charged_not_zero` |
| Fake provider output accepted as live | T6 | `is_mock` refused unless `allow_simulated`; missions labelled SIMULATED | Mitigated — `::test_live_engine_refuses_simulated_output` |
| Tampered receipt or artifact | T7 | HMAC receipts; artifact re-hash before serving | Mitigated — `::test_tampered_receipt_or_artifact_withholds_download` |
| Work continues after emergency stop / restart | all | Persistent latch; gateway, grants and engine check it | Mitigated — `::test_stop_latch_survives_restart` |
| Approval replay / stale approval | T2 | One-time consumption, TTL, principal binding | Mitigated — `::test_expired_approval_is_refused` |
| Approvals lost on restart | T2 | Fail-closed (pending approvals vanish) | **Open (availability)** — durable approval store needed |
| Memory poisoning / cross-user memory | T2 | Memory API owner-only | **Open** (F25) |
| Audit log tampering | — | Engine receipts are HMAC'd; general audit table is not chained | **Open** |
| Self-repair weakens a guardrail (AI proposes editing auth, approvals, sandbox, firewall, X, tests…) | T3 | Protected list (self-development list + sandbox, self-repair itself, HTTP boundary, deployment files, existing tests) checked on every edit; one correction, then NO_RELIABLE_FIX | Mitigated — `selfrepair_service/test_self_repair.py::test_protected_or_invalid_fixes_are_refused_after_one_correction` |
| Self-repair applies an unproven or different fix than the one shown | T2 | Proof in the sandbox (new test fails before, passes after, whole suite passes); apply bound to the proposal's SHA-256, Root Owner only, explicit confirmation; restore point, patch file, undo refuses if files changed since | Mitigated — `::test_a_clear_bug_is_fixed_in_the_sandbox_then_applied_only_with_the_owners_ok`, `::test_a_test_that_does_not_reproduce_the_problem_is_not_proof`, `selfrepair_service/test_self_repair_http.py` |
| Self-repair sends private data to the AI provider | T6 | Code excerpts pass the secret redactor; screenshots only with a separate consent tick; Root Owner only; every report confirmed | Mitigated — `::test_screenshots_need_consent_and_are_read_by_the_model`; the owner must still check screenshots for private content |
| Self-repair changes code in a deployed container | T9 | `/app` is read-only in the image; applying refuses up front (nothing half-changed) | Mitigated — `::test_read_only_code_is_never_half_changed`, CI container check |
| Proposed fix's tests run without isolation (no sandbox on this PC) | T5 | Owner chooses per fix, only when "Run on my PC" is on; runs on a temporary copy | **Accepted by owner per fix** (same trust as "Run on my PC" missions) |
| Sandbox helper fails under gVisor (loopback ioctl refused), tempting a weaker sandbox | T5 | Netlink fallback; if loopback can't come up the command runs without it; the network namespace stays empty | Mitigated — `agents/test_netns_launcher.py`; validated on gVisor in `platform-validation` |

Out of scope until explicitly authorised: public exposure, multi-host deployment, payment and messaging integrations.
