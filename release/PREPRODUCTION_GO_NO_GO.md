# Hood & X — preproduction GO / NO-GO

## Decision: **NO_GO**

| Item | Value |
|------|-------|
| Gated commit | `0a9dd1702327eb1e446650ac12bf03befdbd06af` (branch `claude/hello-mecuky`) |
| Gate evidence | `release/evidence/0a9dd1702327/preproduction_gate.json` (sha256 `d3ea1e9c…c62b`), JUnit alongside |
| Host | Linux 6.18 cloud container, Python 3.13.16, Chromium 1194 (headless), no provider keys, no public internet |
| Full suite | **344 passed, 0 failed, 6 skipped** (all skips `BLOCKED_EXTERNAL`: live provider or public internet) |
| Baseline (imported checkpoint) | 238 passed, 9 failed, 17 errors (`audit/BASELINE.md`) |
| Capabilities accepted | **0 / 26** (acceptance needs target, live-provider or staging evidence) |
| Local release rehearsal | PASS, 16/16 checks (`release/staging/LOCAL_REHEARSAL.json`) — *not* staging evidence |
| Provider mode of all agent runs | **SIMULATED** (scripted model). No live LLM call was made. Spend: $0. |
| Deployment | None. Nothing was deployed, no external service was contacted, X was never activated. |

### Gates

| Gate | Result | Why |
|------|--------|-----|
| G0 baseline | PASS | Clean tree, register matches the 26-item live registry, results recorded |
| G1 P0 security | **FAIL** | Open P0 findings F03, F05, F07, F09, F10, F12, F23, F25, F27, F29; no independent security review |
| G2 functional core | **FAIL** | Golden journey proven only with a scripted model; no approved live provider |
| G3 recovery & finance | PASS | Crash/re-lease, cancel fencing, stop latch across restart, durable spend, parallel reservation tests pass |
| G4 P1 coverage | **FAIL** | No capability has target/staging evidence; 5 business integrations not implemented |
| G5 full suite | **FAIL** | 0 failures, but 6 live tests skipped — skips are missing evidence, not passes |
| G6 deployment | **FAIL** | No staging host; `release/staging/STAGING_EVIDENCE.json` absent |
| G7 independent review | **FAIL** | `audit/INDEPENDENT_REVIEW.json` absent |

## Status by category

### Implemented and verified (locally, on Linux, this commit)
- **Security boundaries:** loopback Host allowlist (blocks DNS rebinding of first-run setup); CSRF on cookie
  sessions; CSP + frame/referrer headers; hashed session tokens; constant-cost login; revoke-by-id.
- **Approvals:** bound to tool, target, task, exact parameter hash and principal; TTL expiry; one-time use;
  every refusal audited (`DENY`).
- **Emergency stop:** persistent latch checked before every tool, grant and agent step; kills sandbox process
  groups; per-subsystem report that never claims a failed or detached halt; root-only release.
- **Multi-agent engine:** validated plan graph, approval bound to plan hash and budget, leased tasks with
  fencing, persisted proposals (crash recovery without a second model call), role-scoped sandbox writes,
  network-isolated test execution, deterministic independent verifier, bounded repair loop, HMAC receipts,
  hashed write-once ZIP artifacts, HTTP API, CLI and UI panel (Chromium at 1440 px and 390 px).
- **Cost control:** operator price table, refusal of unknown-cost paid calls, atomic reservations, durable
  per-mission ledger, missing usage and post-send failures charged rather than recorded as $0.
- **Conversation continuity:** per-principal durable history, restart resume, isolation, erasure.
- **X authority** from the authenticated ROOT_OWNER role (a non-root user named "zak" can no longer pass).
- **Backup/restore:** consistent SQLite backup with manifest hashes, tamper detection, restore only into empty dirs.

### Implemented but unverified
- Gemini/OpenAI adapters and the live router path (no approved key, pricing file or spend cap).
- Vault key from `HOOD_VAULT_KEY` / OS secret store on Windows; Windows ACL checks.
- Browser automation against public sites; Windows Chromium.
- CI workflow (`.github/workflows/ci.yml`): written, not yet run on GitHub (Windows job expected to skip sandbox tests).
- Staging deployment and rollback procedure (`docs/RUNBOOK.md`): written, not executed.

### Simulated (must not be read as live capability)
- Every agent mission in the test suite: plans, code and QA tests come from `tests/agents/scripted_provider.py`.
  The build, test, verification and packaging around them are real.

### Blocked by external dependency
- Live provider journeys (keys, pricing, spend authorisation). Public-internet tests (no outbound access).
- Windows desktop control, microphone/voice, Sentinel host checks, X wake/sleep on the owner's PC.
- Staging host; independent reviewer.

### Not implemented
- Voice STT/TTS (fails closed with text fallback). E-commerce, freelance, marketing, calendar, email integrations.
- PDF/DOCX/XLSX exporters (only ZIP code artifacts). Principal-scoped memory (F25). Durable approvals.
- Hood proprietary model.

### Explicitly deferred and disabled
- **None approved.** Multi-node runtime, learning/evolution and the proprietary model are optional per the
  blueprint, but deferral needs the owner's written decision, and their entry points still exist. Until then
  they count against the gate.

## Residual risks (read before any supervised use)
1. Sandboxed test processes cannot reach the network but **can read the host filesystem**. Run agent
   missions only on a machine or VM with no real data until a container/mount-namespace sandbox lands.
2. A malicious generated app could subvert in-process QA tests (monkeypatching). Mitigation: out-of-process probes.
3. Pending approvals and X state live in memory; a restart drops them (fails closed, but loses work).
4. The general audit table is not tamper-evident (agent receipts are).
5. Legacy commander/NOVA paths still exist and are labelled LEGACY; they are not multi-agent evidence.

## Remediation path to GO (in order)
1. Owner decisions: approve a provider, a pricing file and a spend cap; decide deferral of optional
   capabilities (nodes, evolution, proprietary model) and of the five business integrations, or fund them.
2. Close open P0s: principal-scoped memory (F25), authenticated grant issuer + container sandbox (F05),
   single X controller with persisted state (F12), Windows vault key handling (F07), remove or quarantine
   legacy templated leads (F09), self-evolution/self-healing promotion gates (F10, F29), migration sender
   authentication (F27), desktop approvals on Windows (F23), principal propagation for chat/memory (F03).
3. Run the live golden journey: `HOOD_RUN_LIVE_PROVIDER=1 python hood_cli.py agent create …` → approve → run,
   with budget ≤ the approved cap; record spend against the provider invoice.
4. Owner Windows-machine session (checklist in `docs/RUNBOOK.md`).
5. Staging deployment + backup/restore + rollback rehearsal → `release/staging/STAGING_EVIDENCE.json`.
6. Independent security and architecture review → `audit/INDEPENDENT_REVIEW.json`.
7. Re-run `python scripts/preproduction_gate.py` on the final commit.

## Deliverables index
| Deliverable | Path |
|-------------|------|
| Finding matrix F01–F36 | `audit/FINDINGS_F01_F36.csv` (titles reconstructed; original audit not supplied) |
| Capability acceptance register (26) | `audit/CAPABILITY_ACCEPTANCE.json` |
| Baseline and failure triage | `audit/BASELINE.md`, `audit/baseline/full_suite_baseline.log` |
| Gate (evidence-consuming) and evidence | `scripts/preproduction_gate.py`, `release/evidence/<sha>/` |
| Architecture / trust model | `architecture/ACTUAL_SYSTEM_MAP.md`, `docs/THREAT_MODEL.md` |
| Data and cost policies | `docs/DATA_CLASSIFICATION_AND_RETENTION.md`, `docs/PROVIDER_AND_COST_POLICY.md` |
| Install, staging, backup, rollback, incident runbook | `docs/RUNBOOK.md`, `env.example` |
| Backup/restore and rehearsal tools | `scripts/hood_backup.py`, `scripts/local_release_rehearsal.py` |
| CI pipeline | `.github/workflows/ci.yml` |
| SBOM | `release/SBOM.cdx.json` (`scripts/generate_sbom.py`) |
| UI screenshots | `release/screenshots/agent-missions-{1440,390}px.png` |
| Change log | `release/CHANGELOG.md` |

Older top-level reports (`REMEDIATION_REPORT.md`, `FINAL_RELEASE_STATUS*.md`, `NOVA_*`) are historical and
describe the imported checkpoint, not this commit.
