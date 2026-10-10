# Security batch 1 + Phase 4: results and next-phase decision (2026-10-10)

Branches (nothing merged, nothing deployed, no hosting bought, no paid API calls made):

| Branch | Head | Content |
|---|---|---|
| `claude/security-remediation-1` | `683840d` | Security batch 1 (S1 owner takeover / lockout, S2 data paths + backups, S3 Gemini credentials + accounting, S4 review fixes), ARM64/gVisor validation workflow |
| `claude/phase4-self-repair` | branch head | Everything above, plus Phase 4 (self-repair, Self-development panel, Intelligence live data) and the gVisor sandbox fix |

## 1. Test results

| Where | Result |
|---|---|
| Local, full suite (Phase 4 branch) | 598 passed, 6 skipped (the 6 need live internet or a paid key: off by design) |
| Local, full suite (security branch) | 580 passed |
| GitHub CI (`ci`: Linux and Windows test suites, dependency audit, secret scan, container smoke test, preproduction gate) | green on both branches |
| arm64 runner, full suite with real headless Chromium | 576 passed, 28 skipped (the runner host blocks user namespaces; those sandbox tests run inside gVisor below) |
| gVisor (runsc, systrap) on arm64 and x86 | Python 3.13, Node 22, PHP 8.3 (aarch64) run; HOOD's container is healthy; `unshare -rmn` works; headless Chromium works; HOOD's sandbox tests 5/5 pass (after the loopback fix in this batch) |
| Release rehearsal (clean export, boot, one-time setup code, login, CSRF, chat, fail-closed mission, emergency stop, backup / verify / restore, reboot) | PASS (`release/staging/LOCAL_REHEARSAL.json`) |
| `pip-audit -r requirements.txt` | no known vulnerabilities |
| `bandit` on new code | no medium/high findings (one SQL-construction warning fixed with a column allowlist) |

New regression tests in Phase 4: self-repair service (9), self-repair HTTP + live data (5), chat
offer (1), sandbox loopback fallback (2), self-development diff (1), and a real-Chromium flow (chat
offer, screenshot consent, run checks, apply, undo, self-development diff, live data).

## 2. What Phase 4 delivers

- **Self-repair** (Root Owner only). Tell HOOD in chat (an "Investigate & fix" button appears) or
  use **Self-repair › Report a problem**, optionally with a screenshot sent only after a consent
  tick. HOOD finds the code involved and asks the AI model for a minimal fix plus a new test. It then
  proves the fix in its sandbox:
  - the new test fails on today's code;
  - it passes with the fix;
  - the whole suite still passes.

  If HOOD isn't sure, the report ends as **"No reliable fix found"** with the reason, and nothing
  changes. Otherwise the owner sees the diagnosis, the proofs and the exact diff. **Apply** is bound
  to that exact fix, keeps a restore point and a patch file, and can be undone. **Restart** is a
  separate, confirmed step. Guardrail files and existing tests are never edited.
- **Self-development panel.** Proposals recorded through the self-development API are listed, each
  with its exact diff, approval in Sentinel › Approvals, and Apply.
- **Intelligence live data.** The System map page shows:
  - the models in use and their prices;
  - spend from the durable ledger, by model and by day;
  - the last AI calls;
  - mission success rate and typical duration;
  - sandbox and tools state;
  - self-repair counts.

  The old fixed "scores" (92 / 80 / 80.96, a 12% "frontier gap") are gone. They were never
  measured.

Differences from the plan given earlier:
- No per-report spend cap. Self-repair uses the global daily and monthly caps; each call's
  reservation is visible.
- No automatic Git commit. A patch file and a restore point are kept instead.
- No automatic re-check after restart. The owner restarts, and Undo stays available.

## 3. Remaining blockers and what could not be verified

| # | Item | Status |
|---|---|---|
| 1 | 2FA / passkeys for the owner account | **Missing: required before any internet exposure** |
| 2 | Self-repair against the real free Gemini model | Not run (no paid or live calls allowed in this batch). Tests use a scripted model. The owner's first real report is the first live trial. |
| 3 | Self-repair proofs on Windows | HOOD's WSL sandbox has only Python and pytest, not HOOD's packages. So on Windows a fix stops at "Run checks on this PC" (the owner decides per fix). Installing HOOD's packages into the sandbox is a follow-up. |
| 4 | Windows WSL sandbox | Only tested against a simulated Windows; never on a real PC |
| 5 | WordPress missions | Never run end to end for real (PHP on arm64 verified; full mission not) |
| 6 | Restart after a Windows restart | HOOD must be started again by hand (self-start at sign-in was refused as persistence; not worked around) |
| 7 | Node WebAssembly under HOOD's 2 GiB address-space limit | Fails (agent-run Node tools using WebAssembly) |
| 8 | Ubuntu's packaged Node.js is 18 | Use the official Node 22 build on a server |
| 9 | ARM64 on Hetzner's own hardware | Validated on GitHub's arm64 runners, not on Ampere Altra itself; performance not measured |
| 10 | Open threat-model rows | Approvals lost on restart, audit table not chained, generated code reading host files (needs a mount namespace or VM), memory poisoning |
| 11 | LiveKit voice / iPhone app | Not started. Must reach HOOD only through a VPN or tunnel, never a public port |

## 4. Infrastructure recommendation (updated)

ARM64 evidence: all 39 pinned Python wheels exist for aarch64. The full suite passes on arm64 with
real Chromium. Node 22 and PHP 8.3 work. gVisor runs HOOD's container, its sandbox and Chromium on
arm64 (systrap; Hetzner cloud has no KVM). Chromium uses about 225 MiB idle plus about 45 MiB per
heavy page (measured on x86).

- **CAX21** (4 Ampere vCPU, 8 GB, €10.49/month + €0.50 IPv4) is technically suitable and the best
  price for this load. On 10 Oct it was listed as unavailable.
  - When it returns, rent it hourly first (about €0.017/hour) and run the platform-validation steps
    on it for an hour before keeping it.
- If it stays unavailable:
  - CX33 (x86, 4 vCPU / 8 GB, €8.49 + IPv4): also unavailable on 10 Oct.
  - CPX32 (€35.49): available, but about 3× the price.
- Either way:
  - run HOOD under gVisor (`runsc`, systrap);
  - keep `/app` read-only and the package helper off;
  - reach it only through a VPN or tunnel (Tailscale / WireGuard) until 2FA exists.
- Do not buy anything yet (see the decision below).

## 5. AI model recommendation (quality vs price)

| Budget | Choice | What you get |
|---|---|---|
| **€0 (now)** | Gemini free tier (`gemini-3.8-flash` for self-repair) | Enough for small, clear bugs: wrong text, a broken button, a crash with a clear error. Limited daily requests. The provider may use what you send, so don't send private screenshots. |
| **€5–10 one-off prepay** (first purchase, only if needed) | The same Gemini model on the paid tier | No code change; paid-tier data terms; higher limits. About $0.09 reserved per fix attempt, usually less, so $5 covers dozens of reports. Researched price $0.75 / $3.75 per million tokens, announced to double on 2027-01-01: check Google's price page before paying. |
| **€20–30 / month** (later, only if many reports end "no reliable fix") | A stronger model for the DEEP class | Claude models need a new provider adapter (not in HOOD yet). Cheaper OpenAI / Haiku-class models ($0.10 / $0.50) should first be evaluated on HOOD's own reports. Decide from the Live data page's numbers, not before. |

## 6. GO / NO-GO

- **GO:** merge `claude/security-remediation-1` after the owner's review.
- **GO:** continue development locally on the next phase.
- **GO:** owner trial of self-repair on the free tier (no screenshots with private data).
- **NO-GO:**
  - public internet exposure, production, or buying hosting;
  - connecting LiveKit or the iPhone app directly to HOOD's backend.

  The conditions to lift this:
  - 2FA;
  - a staging run on the real server type (hourly rental);
  - VPN/tunnel-only access;
  - closing the open threat-model rows that matter for a server: durable approvals and a chained
    audit log.

Phases left: **2** (OpenAI key, local Ollama model, firewall page, connector catalog), **3**
(hands-free voice, X's identity / theme / voice, X stays active), **5** (Level 3 groundwork).
