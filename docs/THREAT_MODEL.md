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
| Distributed password guessing against the owner from many addresses | T1 | Strong-password rule + scrypt; per-address limits | **Open (residual)** — add 2FA/passkeys before public exposure |
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

Out of scope until explicitly authorised: public exposure, multi-host deployment, payment and messaging integrations.
