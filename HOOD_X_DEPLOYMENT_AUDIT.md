# HOOD & X — Infrastructure and Deployment Readiness Audit

**Audited:** `tariksabouny96-dot/dracula`, branch `claude/eager-dirac-xxzqst`, head `cbb577a` (read-only; source tree untouched, tests run on a scratch copy).
**Date:** 2026-10-10. **Nothing was deployed, purchased, or modified. No secret values were read or are reproduced here.**

Labels used throughout: **[V]** verified by me (code read or measurement run in this audit) · **[D]** documented by the repo but not independently verified · **[A]** assumption · **[R]** recommendation.

> Scope note: the repo named `dracula` (main) is empty; the project lives only on branches `claude/eager-dirac-xxzqst` (latest) and `claude/hello-mecuky` (older). `dracula1` is an empty repo. [V]

---

## 0. Executive verdict

| Target | Verdict |
|---|---|
| **Private staging** (VPN/SSH-tunnel only, no real data, single VM) | **CONDITIONAL GO** — after blockers B1–B4 below are fixed |
| **Public or production** | **NO-GO** |

The codebase is unusually disciplined (512 tests pass, fail-closed design, honest labelling), but the **cloud packaging has verified defects** (public first-run takeover, missing persistence, broken credential wiring) and the agent sandbox does not isolate the host filesystem. The repo's own gate says NO_GO, and I agree with it. [V][D]

---

## 1. Verified architecture

```
                      Internet
                         │ 443 (TLS, Caddy — compose only)
                         ▼
        ┌─────────────────────────────────────────────┐
        │ Container "hood" (python:3.13-slim, uid 10001)│  Caddy shares its netns
        │                                             │  ⇒ every client appears as 127.0.0.1  ⚠ B1
        │  ui/server.py  ThreadingHTTPServer 127.0.0.1:8990 (single process, thread/conn)
        │   ├ auth: scrypt, hashed sessions, RBAC, CSRF, Host allowlist, CSP
        │   ├ /api/chat ─► InteractionService ─► ModelRouter ─► Gemini (live) | OpenAI | Local | Mock
        │   ├ /api/agents ─► AgentEngine  (the real multi-agent path)
        │   │     planner → specialists(engineer/QA/reviewer) → Workspace sandbox
        │   │     (unshare -rn netns, rlimits, role write-roots) → verifier → HMAC receipts → ZIP artifact
        │   ├ /api/approvals ─► ApprovalService (in-memory, TTL, one-time)
        │   ├ /api/x/* ─► XSessionManager (ROOT_OWNER + L4 approval + TTL; state persisted, fails closed)
        │   ├ firewall (default-deny egress policy — application-level, cooperative)
        │   ├ voice (Gemini STT/TTS, optional ElevenLabs, browser fallback)
        │   ├ learning/memory/exports/artifacts/self-dev (owner-approved patches)
        │   └ legacy: HoodCommander, DAG scheduler, operations/mission_service (labelled LEGACY)
        │  Playwright Chromium (browser automation), dev_executor, desktop (Win32 only)
        └──────── volume /data = HOOD_DATA_DIR ────────
        SQLite (WAL) ×~14 files; some under /data, others cwd-relative `artifacts/` and ~/.hood/nova21 ⚠ B3
```
Sources: `architecture/ACTUAL_SYSTEM_MAP.md` [D] cross-checked against `ui/server.py` (bind at line 1464, init guard at 174–177), `services/agents/*`, `services/model_gateway/*`, DB grep. [V]

**Not present at runtime [V]:** no queue/broker, no Redis, no Postgres (`infra/database/schema.sql` and the "PostgreSQL" backend are unused/simulated), no GPU code path, no worker process separate from the web process, no metrics endpoint.

**Independent scaling units today:** none (monolith + SQLite). **Separable with modest work [R]:** (a) agent sandbox executor, (b) Chromium/browser automation, (c) TLS/proxy, (d) local-model server.

---

## 2. Technology and dependency inventory [V]

- **Language/runtime:** Python 3.13 (Docker `python:3.13-slim-bookworm`; CI 3.13). ~27,960 LOC source, ~11,820 LOC tests, 34 service packages. Frontend: static HTML/JS (no build step; Node only for `node --check` in CI).
- **Python deps (39 pinned, `requirements.txt`):** pydantic 2.13, httpx 0.28, cryptography 50, playwright 1.63, PyYAML, psutil, pypdf, python-docx, openpyxl, fpdf2, lxml, pillow, aiosmtpd, pytest stack. `pip-audit -r requirements.txt` today: **no known vulnerabilities**. SBOM committed (`release/SBOM.cdx.json`). Note: no hash-pinning/lockfile with hashes; `aiosmtpd` presence is unexplained [A: unused or test-only].
- **Datastores:** SQLite only. Files: `auth.db`, `agents/agent_engine.sqlite3`, `conversations.sqlite3`, audit/memory `artifacts/hood_data.db`, plus learning, self_dev, exports, registry, firewall, economic, intelligence, lab, acceleration, impossible_list DBs.
- **Infra files:** `Dockerfile`, `docker-compose.yml` (hood + Caddy 2.8), `deploy/Caddyfile`, GitHub Actions `ci.yml` (Linux full suite + Windows import/compile check, pip-audit, gitleaks, SBOM, docker build + health smoke test, manual release gate).
- **CI status [V via GitHub API]:** latest run (#37, head `cbb577a`) **success**; 37 runs on record.
- **Stale docs [V]:** `docs/CLOUD_DEPLOYMENT_PLAN.md` references `main.py` (does not exist), Python 3.12 and a Postgres+pgvector target that is not implemented. Do not follow it. The "390 passed" figure in the GO/NO-GO doc is also out of date (see §5).

---

## 3. AI provider and execution-method inventory

| Provider / model | Method | State | Evidence |
|---|---|---|---|
| **Google Gemini** `gemini-3.8-flash` (standard/deep), `gemini-3.5-flash-lite` (fast + fallback) | Remote REST API (stdlib HTTP, API key) | **ACTIVE** — chat, mission planning, specialists | Adapter and tests [V]; live mission COMPLETED/PASS, provider_mode LIVE, $0 recorded, 25 s, evidence file committed 2026-10-09 [V as artifact; I did **not** re-run live]. Commit log reports a 429 free-quota exhaustion in chat [D]. |
| Gemini STT (`gemini-3.5-flash-lite`), TTS (`gemini-3.8-flash-tts`), embeddings (`gemini-embedding-001`) | Remote REST API | **ACTIVE if key present** | `services/voice/cloud.py`, `learning/embeddings.py` [V code] |
| `gemini-3.1-pro-preview` | — | **Priced only**, not a default | pricing JSON [V] |
| **OpenAI** (default `gpt-5-mini` in adapter; `gpt-4o`/`gpt-4o-mini`/`o3-mini` in config template — inconsistent) | Remote REST API | **Configured, disabled** (needs `HOOD_ALLOW_OPENAI_API_CALLS=1` + key + price). Never live-tested (ledger F18). | [V] |
| **Local OpenAI-compatible** (Ollama default `llama3:8b` @127.0.0.1:11434) | Local HTTP, **loopback-only enforced** | **Configured, disabled**, never tested (F17). Cannot target a remote GPU box without code change. | `local_adapter.py` [V] |
| **ElevenLabs TTS** | Remote API, key in vault, owner opt-in via Settings | Optional | [V code] |
| **Mock** | In-process | Tests / simulated; refused by live engine unless explicitly allowed | [V] |
| **Anthropic/Claude, OpenAI Codex, desktop subscriptions** | — | **No runtime integration.** `ProviderName.ANTHROPIC` is an enum value with no adapter; grep for anthropic/claude/codex finds nothing else in runtime code. Claude/Codex are *development* tools only (commits are Claude co-authored). | [V] |
| `evolution` registry entries (`gemini-2.5-flash`, `llama3-8b-local`) | Static metadata | Inactive; code comment says 2.5 models 404 for new keys | [V] |

Key policy [V]: "no price, no call" — paid calls are refused without a price in `HOOD_MODEL_PRICING`; reservations are atomic; mock output is refused as live. **Caveat:** the shipped price file declares **$0 for every model** on an owner-asserted free-tier key. If billing is ever enabled, cost tracking will silently under-count until prices are edited. [V][R]

---

## 4. Measured infrastructure requirements

Measurement host: 4 vCPU Intel Xeon 2.8 GHz, 16 GB RAM, no GPU, Linux 6.18, Python 3.13.16. Scripts: `measure.py`, `load.py` in the session scratchpad. **Not measured:** live-model mission resource use (no key/approval), Docker image size and container behaviour (no Docker daemon in this environment), multi-user concurrency of real missions.

| Measurement [V] | Result |
|---|---|
| Full test suite (incl. 4 headless-Chromium UI tests, sandbox tests) | 230 s wall; 130 CPU-s (avg 0.56 cores); **peak 1.67 GB RSS** across the process tree (11 procs); largest single process 368 MB |
| Server process, just after start | ~85 MB RSS |
| Server after ~7,000 requests | **318 MB** RSS/HWM (+233 MB; growth source not investigated — [A] caches/thread stacks, rule out a leak in staging soak) |
| Throughput `GET /api/auth/status`, keep-alive, 1 / 10 / 50 / 200 connections | 441 / 506 / 352 / 231 req/s; p50 1.9 / 11.7 / 16.4 / 20 ms; **p99 12 / 35 / 1,487 / 4,688 ms**; 0 errors |
| Static `/index.html`, 20 conns | 401 req/s, p99 1.0 s |
| Sandbox per child (code constants) | CPU 120 s, address space 2 GB, files 32 MB, 256 FDs, 64 KB output cap |
| `unshare -rn` available on this kernel | yes (it will **not** be in default Docker or by default on Ubuntu 24.04 — see §6/§7) |
| Scripted mission wall time (tests) | ~5–8 s; the committed live mission took 25 s |

**Derived sizing [A/R]:** the control plane is I/O-bound and single-process (GIL, thread per connection): **1–2 vCPU and 1 GB suffice for the web/API tier**; tail latency degrades past ~50 concurrent connections, which is irrelevant for a single-owner tool. The real memory driver is **Chromium + sandboxed pytest children**: budget ~2–3 GB for one mission plus one browser session, ~1 GB per additional parallel mission (children are capped at 2 GB virtual each). Disk: code+venv+Chromium image is multi-GB [A, unmeasured]; data is small (SQLite + ZIP artifacts) — 20–40 GB is ample. **GPU: not needed** unless local models are adopted; no code path currently uses one. Network: outbound HTTPS to `generativelanguage.googleapis.com` (plus optional OpenAI/ElevenLabs); inbound 443 only.

---

## 5. Deployment-readiness assessment

**Test evidence (my run) [V]:** 512 passed, 6 skipped (all live-provider/live-network, correctly marked BLOCKED_EXTERNAL), **1 failed** — `test_tool_gateway_capabilities_and_execution` failed solely because my scratch copy lacked `.git` (exit 128 from `git status`); re-run in a real checkout: **passes**. Net: green. Layers present: unit, integration, adversarial/security, browser E2E (Chromium, 2 viewports), resilience/crash-recovery, backup/restore, local staging smoke, release gate. **Absent: load/soak tests, container-sandbox tests, real-proxy end-to-end tests, restore-into-new-host drill, live provider contract tests in CI.**

### Blockers, ranked

| # | Sev | Finding | Status |
|---|---|---|---|
| **B1** | **Critical** | **First-run owner takeover through the proxy.** `/api/auth/init` is allowed when the TCP peer is loopback (`ui/server.py:174-177`). In the compose topology Caddy shares the netns, so *every internet client* arrives from 127.0.0.1. I reproduced the request shape (loopback source + public `Host`) against a scratch instance: **a stranger became ROOT_OWNER**; a second attempt was rejected. Anyone who finds the host before the owner initialises it owns Hood and X. Same cause: access logs and any per-IP lockout see only 127.0.0.1 (login uses the same `client_address`). | [V] (simulated proxy; real Caddy not run) |
| **B2** | **Critical** | **Agent sandbox does not isolate the host filesystem** (repo says so). Generated code runs as the same uid and can read `/data` DBs, key files (0600 owned by that uid), and the vault. Do not run missions on a host holding real data. Compose's suggested fix (`seccomp:unconfined`) removes Docker's main barrier. In default Docker, `unshare -rn` is blocked and missions end UNVERIFIED. | [V][D] |
| **B3** | **High** | **Persistence mismatch.** Only `/data` is a volume. Audit/memory DB and `vault.enc` default to cwd-relative `artifacts/` (→ `/app`, container layer), and `MissionService` uses `~/.hood/nova21` (→ `/home/hood`). Recreating the container loses audit log, memory and vault; `hood_backup.py` also covers only `HOOD_DATA_DIR`. | [V] by code; container not run |
| **B4** | **High** | **Credentials/pricing not wired.** Compose passes `HOOD_GEMINI_CREDENTIAL`, but the code only honours the literal value `proxy` there; a real key needs `GEMINI_API_KEY` or the vault, which compose doesn't pass. `HOOD_MODEL_PRICING` defaults to empty, so every paid call is refused as "unknown cost". As shipped, the container cannot call a model. | [V] |
| B5 | High | **Open P0 ledger items:** F03, F05, F07, F10, F12, F23, F29; gate G6 (no staging evidence) and G7 (no independent security review) fail. Plus legacy self-evolution/healing promotion needs redesign. | [D] |
| B6 | High | **Egress firewall is cooperative.** It is consulted by the model router, voice and embeddings; grep finds **no references** in browser, tool_gateway, internet_intelligence or dev_executor. Chromium and other paths are not constrained by it. Need host-level egress rules. | [V] |
| B7 | Medium | State lost on restart: pending approvals (in memory), X active session (fails closed — good). Single process, no HA; SQLite ⇒ one writer node. | [V][D] |
| B8 | Medium | No metrics, structured error alerting, log shipping, or provider-spend alerts outside the app; only an HTTP health probe. No load tests. | [V] |
| B9 | Medium | Windows-first features (desktop control, "Run on my PC", WSL2 scripts) are meaningless in the cloud; the owner's real validation is on a Windows PC. Cloud = Linux subset. | [V][D] |
| B10 | Low | Doc drift (cloud plan, test counts), OpenAI model default mismatch, audit table not tamper-evident, no retention/rotation policy. | [V][D] |

**Secrets hygiene [V]:** no tracked `.env`, key, vault or DB files; pattern scan of non-test sources found no key-shaped strings; two test files contain key-shaped strings, presumably fixtures for the redactor (not inspected value-by-value). Image excludes `.env` via `.dockerignore`; gitleaks runs in CI.

**X governance [V code/tests, D docs]:** dormant by default; activation needs ROOT_OWNER role (not a username), an approval (TTL, one-time, principal-bound), scope YAML with in/out-of-scope and capability switches (exploitation etc. disabled in the example); state persisted, expired sessions forced dormant on restart; sealed memory excluded from migration archives. Residual: legacy `XExecutiveController` still hard-codes `user == "Zak"` (legacy path); scope enforcement only constrains tools that call it — **keep X disabled in the cloud** until B2/B6 are closed and any engagement targets are reachable only from an isolated worker.

---

## 6. Local vs cloud vs hybrid

| | Local-only (Windows PC) | Cloud-only | **Hybrid (recommended)** |
|---|---|---|---|
| Cost | ~$0 + Gemini | VM ~€10–50/mo [A] + LLM | same as cloud + PC |
| Performance | Fine; PC desktop features work | Fine for API tier | Fine |
| Reliability | Off when PC is off; no sandbox on Windows | 24/7 but single node, SQLite | 24/7 core; PC optional |
| Security | Real data and sandbox-less execution on the daily PC | Public attack surface, B1–B2 | Core private (VPN), sandbox isolated, PC trusted-node only |
| Dev productivity | Claude/Codex native | Slower loop | Local dev with Claude/Codex → GitHub → CI → staging |
| Fit with code | Original target | Linux subset, no desktop control | Matches the repo's node/migration concept (itself **deferred/unreviewed**, F27/F35) |

**[R] Hybrid:** develop locally with Claude/Codex; run the always-on Hood core (chat, missions, memory, approvals) on Linux; keep desktop/Windows control strictly on the owner's PC. Don't use the multi-node transport until the owner un-defers it and it has been reviewed.

---

## 7. Hetzner CX43 evaluation

Hetzner list prices and specs were **not verified** (no web check). [A] CX43 ≈ 8 shared vCPU / 16 GB RAM / 160 GB NVMe in the low-teens € per month; confirm in the Hetzner console.

- **Capacity vs measured need:** comfortably above requirement (§4: web tier ≤1 GB; one mission + Chromium ≈ 2–3 GB). **Oversized for staging**; a 4 vCPU / 8 GB class suffices. CX43 is acceptable if you want one box to also host the sandbox worker and a staging copy. [R]
- **Constraints:** shared vCPU (noisy-neighbour variance, fine for staging); no GPU; one node = no HA.
- **Sandbox caveat [A, test on first boot]:** Ubuntu 24.04 restricts unprivileged user namespaces via AppArmor, which would break `unshare -rn`; Debian 12 or a sysctl change is needed — and then B2 still applies.
- **Alternatives:** (1) **Staging:** CX43 as-is, or a smaller CX/CPX if cost matters. (2) **Production:** dedicated-vCPU class for consistent latency, plus a **separate small sandbox worker VM** with no secrets and egress-only to nothing. (3) **GPU/local LLM:** only if cost/privacy data justify it; dedicated GPU servers are a very different price tier [A] and the local adapter needs changes to reach a non-loopback host. Gemini API is the cheaper default.

### Monthly cost scenarios (infra only; LLM cost is parametric because no token usage was measured and prices are not in the repo)

| Scenario | Components | Infra/month [A, verify] |
|---|---|---|
| Staging-min | 1× small VM (4 vCPU/8 GB), Hetzner snapshots, Tailscale/WireGuard free | ~€8–12 |
| **Staging (requested)** | CX43 + automated backups + Storage Box/S3 for backups | ~€15–25 |
| Production-lean | CX43 (core) + small sandbox worker VM + backups + domain | ~€30–50 |
| Production-robust | dedicated-vCPU core + sandbox worker + managed backup + monitoring SaaS | ~€80–150 |
| GPU add-on (optional) | dedicated GPU server | hundreds €/mo — not justified by current evidence |

**LLM cost = Σ(mission input_tokens × P_in + output_tokens × P_out) + chat + voice + embeddings.** The ledger already records tokens/cost per mission; run 20–30 representative missions on staging and read the ledger before committing to a budget. The **free tier is not a production plan**: chat already hit 429 daily quota. [D]

---

## 8. Migration and implementation plan (no execution performed)

**Phase 0 — Decisions (owner):** approve provider + paid-tier plan + spend cap; keep X disabled in cloud; defer multi-node/evolution/proprietary model in writing; choose private access (VPN) for staging.
**Phase 1 — Fix blockers in a branch (see §9):** B1, B3, B4 first (small), then B6, then sandbox isolation (B2).
**Phase 2 — Staging:** provision VM (firewall: only SSH-from-your-IP/VPN; no public 443), install Docker, deploy by image digest, initialise owner **via CLI on the host**, run smoke + backup/restore drill + rollback drill, 24–72 h soak. Capture `release/staging/STAGING_EVIDENCE.json` (the gate expects it).
**Phase 3 — Live validation:** `HOOD_RUN_LIVE_PROVIDER=1` journey with capped budget; reconcile ledger vs the provider invoice.
**Phase 4 — Independent security review** (gate G7) incl. pen-test of the proxy topology and sandbox escape attempts.
**Phase 5 — Production:** protected environment, manual approval, public exposure only after B1/B2 closed and review passed.

---

## 9. Required code/config changes (not executed)

1. **`ui/server.py:174-177`** — replace the loopback-IP check with a bootstrap secret (e.g. one-time token printed by CLI / env `HOOD_BOOTSTRAP_TOKEN`) or disable HTTP init when `HOOD_ALLOWED_HOSTS` is set; add trusted-proxy handling (`X-Forwarded-For` honoured only from a configured proxy) so audit logs and lockouts see real clients (also lines ~206, 261, 312).
2. **`docker-compose.yml`** — pass `GEMINI_API_KEY` via a secret file, set `HOOD_MODEL_PRICING=/app/config/...`, keep `HOOD_GEMINI_CREDENTIAL` only for proxy mode; document it in `env.example`.
3. **Persistence:** make `artifacts/` (`hood_data.db`, `vault.enc`, other DBs) and `MissionService`'s `~/.hood/nova21` resolve under `HOOD_DATA_DIR`; extend `scripts/hood_backup.py` to include them; add a restore test on a clean directory.
4. **Sandbox:** run mission execution in a separate worker (container/microVM with no data mounts, read-only root, no network, seccomp profile permitting only needed namespaces) instead of `seccomp:unconfined` in the main container; add tests that a child cannot read the data dir.
5. **Firewall:** enforce at host level (nftables/Hetzner firewall allow-list) and wire the app firewall into browser, tool_gateway, internet_intelligence, dev_executor.
6. **Pricing guard:** refuse start (or warn loudly) if billing is enabled but the price file is all zeros.
7. **Local adapter:** allow a configured private endpoint (mTLS/WireGuard) only if a GPU node is ever adopted.
8. **Operations:** `/metrics` (auth-protected) with spend, 429 rate, mission states, queue/lease age; structured JSON logs; durable approvals; audit hash-chain; retention job.
9. **Housekeeping:** fix `CLOUD_DEPLOYMENT_PLAN.md`, update test counts, reconcile OpenAI defaults, add load/soak and container tests to CI.

---

## 10. Pipeline: GitHub + Claude + Codex + staging + protected production [R]

- **Local:** Claude/Codex work on feature branches; secrets only in local `.env`/vault, never given to agents; `pytest` before push.
- **GitHub:** protect `main` (PR required, CI green, CODEOWNERS = owner, no force-push; agents cannot push to `main`). Existing `ci.yml` stays; add: container sandbox-isolation tests, compose smoke with a real proxy, gitleaks on PRs, dependabot/pip-audit schedule.
- **Build:** on merge to `main`, build the image, sign/tag by digest, push to a private registry (GHCR).
- **Staging:** auto-deploy by digest via GitHub Environment `staging` (deploy key/OIDC, no long-lived prod secrets), then smoke + synthetic mission with a tiny budget.
- **Production:** GitHub Environment `production` with **required owner approval**, separate VM, separate provider key and spend cap, secrets only there. Claude/Codex have no access to this environment.
- **Rollback:** deploy previous digest; DB restore from the last verified backup (take a backup before every deploy; migrations must be backward-compatible or restore-gated).

---

## 11. Cost control, monitoring, incident response [R]

- **Layers:** (1) in-app reservations/ledger/mission caps [V exist]; (2) provider-side budget + hard quota/alerts at Google Cloud; (3) daily reconciliation job comparing ledger vs billing export; (4) separate keys for staging/prod/dev with per-key caps; (5) alert at 50/80/100% of daily and monthly.
- **Monitoring:** uptime probe on `/api/auth/status`; host CPU/RAM/disk; alerts on 429 rate, failed missions, sandbox-unavailable, emergency-stop engaged, X activated, new login from new IP, backup age > 24 h.
- **Incident response:** (a) **Emergency stop** (exists; persists across restarts) → (b) revoke/rotate provider key → (c) snapshot disk for forensics → (d) rotate `HOOD_VAULT_KEY`/`HOOD_RECEIPT_KEY` (`SecretVault.rotate_key()` exists) → (e) restore last good backup on a fresh VM → (f) post-incident review. Run a game-day for stop, key rotation and restore before production.
- **Backups:** `scripts/hood_backup.py` (manifest hashes, tamper detection, restore only into empty dir) [V in tests] — extend per B3; encrypted off-host copy; quarterly restore drill; keys backed up separately from data.

---

## 12. Final recommendation

**CONDITIONAL GO — private staging only**, on a single Hetzner VM (CX43 is acceptable, a smaller class is sufficient), reachable only over VPN/SSH, with no real personal data, X disabled, and missions disabled or confined to a secrets-free worker, **after** B1 (init takeover), B3 (persistence), and B4 (credential/pricing wiring) are fixed and re-verified.
**NO-GO for production/public exposure** until B2 (sandbox isolation), B6 (egress enforcement), the open P0 findings, a staging soak with backup/restore/rollback evidence, and an independent security review are complete.

### What I could not verify
Live Gemini behaviour (not re-run; no approval/spend authorised), Docker image build/size and container runtime (no daemon here — though CI's docker-build job is green on the head commit), real Caddy proxying (simulated at request level), Windows features, Hetzner prices/specs, and the cause of the ~230 MB server memory growth under load.

---

## 13. Addendum — Hood Levels 1, 2 and 3 (follow-up audit)

Added after the owner asked whether the three model levels were considered. The first version covered Level 1 only in depth.

### 13.1 What the levels are, and what actually exists [V]

| Level | Intent (`services/evolution`, `services/lab`) | Reality in this commit |
|---|---|---|
| **L1 External** | Frontier API (Gemini) as teacher/primary | **Live.** Everything in §3–§5 applies. |
| **L2 Self-hosted** | Ollama/vLLM/llama.cpp, e.g. `llama3-8b-local`, needs ≥ 8 GB VRAM single device; for private/routine work at $0 API cost | Adapter exists but is **disabled by default** and **never tested** (F17). Endpoint and model are **hard-coded defaults** (`127.0.0.1:11434`, `llama3:8b`): the router builds `LocalProviderAdapter(enabled=…)` with no endpoint/model from config, and the example config has no `local` provider block. Loopback-only enforced. |
| **L3 HOOD-owned** | QLoRA-fine-tuned `hood-code-candidate-v1` on top of L2, ≥ 12 GB VRAM, starts in SHADOW | **Does not exist.** No training code or ML libraries anywhere (no torch/transformers/peft/vllm/llama.cpp in code or requirements). What exists is governance scaffolding: experience collector, dataset builder (JSON; X_SEALED excluded), model arena, promotion/drift/calibration logic, hardware planner. The chat layer itself tells the owner "Level 3 … does not exist yet". |

Further facts [V]:
- **The levelled router is not on the live request path.** `EvolutionEngine`/`CostAwareEvolutionRouter` is instantiated by the CLI runtime and used only by `evolution-*` inspection commands and the emergency-stop wiring. Real chat and missions go through `services/model_gateway/router.py` (provider order Gemini → Local → OpenAI → Mock). So "route private tasks to L2/L3, high-risk to L1 + checker" is **designed, not enforced**.
- **Registry shows models that are not installed.** `llama3-8b-local` is registered as SECONDARY and the L3 candidate is listed, on a **simulated** RTX 4080 / 32 GB profile; the registered L1 is `gemini-2.5-flash` (the code elsewhere notes 2.5 models 404 for new keys; real defaults are 3.8-flash / 3.5-flash-lite). The registry and the live provider settings disagree.
- **Bug:** `python hood_cli.py evolution-breakeven` **crashes** (`AttributeError: 'Namespace' object has no attribute 'api_spend'`, `hood_cli.py:826`; the argument is never defined). The financial tool the owner would use to decide on GPUs does not run today.
- **Name collision:** `Level3AccelerationEngine` is a separate "learning acceleration" mechanism (lessons/skills in SQLite), not the Level-3 model. It adds another cwd-relative DB (`artifacts/acceleration_engine.db`) to the persistence problem (B3); `artifacts/evolution/*` likewise.
- These modules are **deferred/optional** in the ledger (F30, F35) but their entry points remain enabled; the gate counts that against you until the owner defers them in writing.

### 13.2 What each level needs from infrastructure [A unless noted]

| | Compute | Network/security | Verdict |
|---|---|---|---|
| **L1 only (today)** | CPU VM, no GPU (§4) | Outbound HTTPS to Google | Ready for staging after B1–B4 |
| **L2 on the Hood VM** | An 8B model quantised to 4-bit needs roughly 5–6 GB of memory; on CPU-only 8 vCPU expect single-digit tokens/second [A, unmeasured] — usable for short private tasks, too slow for multi-agent coding missions | Loopback today ⇒ same VM only | Feasible as a *private fallback*, not as a productive coding engine; quality for missions is **unknown** — must be benchmarked before any routing to it |
| **L2 on a separate GPU box** | GPU with ≥ 8 GB VRAM (registry requirement), 12–16 GB more comfortable | Needs a code change (configurable endpoint) plus WireGuard/mTLS; `validate_local_endpoint` currently rejects any non-loopback host — keep that rule and add an explicit, authenticated "private node" mode rather than loosening it | Defer until L2 quality is proven |
| **L3 training** | ≥ 12 GB VRAM single device (registry); QLoRA run is hours, not continuous — rent per run, don't own | Training data = mission experiences ⇒ privacy review; X_SEALED exclusion needs independent review; **no trainer exists**, so this is a build project, not a deployment | Not deployable; plan as a separate programme |

### 13.3 Break-even (using the repo's own defaults and formula, since its CLI is broken)

Defaults in the repo: purchase $1,600, 150 GPU-hours/month, rental $0.79/h, 24-month depreciation, 350 W, $0.15/kWh. These are **repo placeholders, not market data** — replace with quotes.
- Rented: 150 h × $0.79 ≈ **$118.5/month**.
- Owned: $1,600/24 ≈ $66.7 + power ≈ $7.9 ⇒ **≈ $74.6/month**; break-even vs rental ≈ 14.5 months (and a purchased GPU does nothing for a cloud-only deployment).
- Versus Gemini: the free tier is $0; paid Gemini Flash-class tokens are cheap [A]. A GPU only beats the API if monthly token spend exceeds the monthly GPU cost (~$75–120 on these placeholders) **or** privacy requires local processing. Current measured evidence (one live mission: ~5 calls, $0) gives **no case yet** for L2/L3 on cost grounds.

### 13.4 Recommendations [R]

1. **Stage with L1 only.** Keep L2/L3 disabled in staging; state that explicitly in the deployment config so the registry cannot imply otherwise.
2. **Run an L2 evaluation before buying anything:** install Ollama on a throwaway GPU instance or the owner's PC, point the adapter at it, and run the existing golden-journey missions plus a private-task set. Record pass rate, tokens/s, and failure modes. Only if L2 passes the verifier at an acceptable rate do you size GPU capacity.
3. **Decide the privacy rule first.** If "private tasks must never leave" is a hard requirement, then without L2 those tasks must be refused in the cloud — today nothing enforces that routing.
4. **Wire or retire the levelled router.** Either make `ModelRouter` consult the evolution routing policy (with tests) or mark the evolution router as inactive in the UI/CLI so it cannot be mistaken for live behaviour.
5. **Fix before relying on it:** `evolution-breakeven` crash (add `--api-spend`), registry/live model mismatch, configurable L2 endpoint/model via config + env (private authenticated node only), move `artifacts/evolution` and acceleration DB under `HOOD_DATA_DIR`.
6. **L3 as a separate programme:** needs a real trainer, an evaluation arena against L1 on held-out missions, data-governance sign-off and rented-GPU approval gates (the approval/spend-gate abstractions exist). Revisit after months of verified mission data exist.

### 13.5 Effect on the verdict
No change: **CONDITIONAL GO for private staging with L1 only; NO-GO for production.** The levels add two items to the blocker list: **B11 (Medium)** — levelled routing is not enforced on the live path, so privacy-based routing guarantees don't exist; **B12 (Low)** — broken `evolution-breakeven` CLI and registry that advertises uninstalled/simulated models.

*Not verified:* L2 model quality or speed on any hardware, any GPU provider's pricing, and whether a trained L3 model could ever outperform L1 for Hood's tasks.
