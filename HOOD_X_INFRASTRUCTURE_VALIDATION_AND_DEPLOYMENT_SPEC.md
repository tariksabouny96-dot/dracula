# HOOD & X — Infrastructure Validation, Cost Audit & Deployment Specification

**Date:** 2026-10-10 · **Mode:** read-only. Nothing purchased, deployed, exposed or modified. No secret values read or reproduced.
**Source audited:** `tariksabouny96-dot/dracula`, branch `claude/eager-dirac-xxzqst`, **head `33a3b3d`** (the prior audit covered `cbb577a`; the branch moved while this was being prepared, so the delta was re-audited — see §0.2).
**Classification:** **[V]** VERIFIED by me (code read, measurement, or fetched provider page, today) · **[D]** DOCUMENTED (stated by a source I could not independently confirm) · **[A]** ASSUMPTION · **[R]** RECOMMENDATION.
Currency: EUR excl. VAT unless stated. USD→EUR conversion used where needed: **1 USD = 0.92 EUR [A]**.

---

## 0. Executive summary and decision

**Decision (details §12):**
- **CONDITIONAL GO — start Phase 1 engineering now** (no spend required).
- **CONDITIONAL GO — provision private staging only after** Phase 1 gate G1 passes **and** purchasability is re-verified at order time **and** you approve spend.
- **NO-GO — production or any public exposure.**

**I agree with the two-VM idea, but not with the proposal as written.** Five corrections matter most:

1. **The specified instances cannot be assumed purchasable — and neither can the alternatives, until checked in the order flow.** Hetzner's own cost-optimized and regular-performance pages show *every* listed type as "not available", **including CAX and CPX** [V as fetched]. An independent stock tracker (observed 2026-10-05) is more granular: CX23/CX33/CX43/CX53 **sold out** in all EU datacentres, while **CAX, CPX and CCX are available** [D: third-party, 5 days old]. These two sources conflict for CAX/CPX, so the Hetzner pages cannot be used alone as an availability signal in either direction. CX has been scarce since at least 2026-09-02 [D]. → Re-base the plan on **CAX (Arm)** with a CPX x86 fallback, **both conditional on a live stock check in the console/API at order time**.
2. **The architecture needs software that does not exist yet.** The agent sandbox is an in-process, local-subprocess class (`services/agents/sandbox.py`). Nothing in the engine can execute on a second VM today [V]. The existing node/remote-tool code is a separate, deferred, unreviewed feature that ships signed *tool envelopes*, not workspaces [V]. A **remote executor** must be built (§5, task T-20). Until then VM2 would sit idle.
3. **Core does not need 16 GB.** Measured: server 85 MB at start → 318 MB after 7,000 requests; full test suite peak 1.4 GB including Chromium [V]. A 4 vCPU / 8 GB core is ample.
4. **The budget omits or mis-sizes several lines** (second IPv4, VAT, off-site storage over-estimate, LLM spend — the largest variable —, Gemini's price doubling on 2027-01-01, Tailscale licence terms). Corrected in §3.
5. **Three verified security defects still exist at the new head** (public owner takeover behind the proxy, owner lock-out by a remote attacker, state outside the persistent volume) and the new toolbox feature adds a privilege-escalation surface that must stay disabled in the cloud (§5).

### 0.1 What I verified today (headline evidence)
| Evidence | Result |
|---|---|
| Test suite at head `33a3b3d` (4 vCPU box) | **527 passed, 6 skipped (live-only), 1 failed** — the failure is `test_tool_gateway_capabilities_and_execution` (needs `.git`; my scratch copy had none; passed in a real checkout earlier) · 185 s · avg 0.42 cores · **peak 1.39 GB RSS** (11 processes) |
| CI (GitHub Actions) on prior head | success (run #37); not re-queried for `33a3b3d` |
| `pip-audit` on `requirements.txt` | no known vulnerabilities |
| Arm64 feasibility of Python deps | **all 39 pinned requirements have `aarch64` wheels for CPython 3.13** (incl. pydantic-core, cryptography, lxml, pillow, psutil, playwright) [V]; Arm Chromium itself **not tested** [A] |
| Chromium memory (headless, trivial page, PSS) | 1 browser ≈ **640 MB**; 2 ≈ 854 MB; 3 ≈ 1,180 MB (≈ +200–330 MB each extra) [V] |
| Sandboxed pytest child (`unshare -rn`) | ≈ 111 MB each; 4 in parallel peak ≈ 340 MB total [V] |
| HTTP server throughput | ~440–500 req/s up to 10 connections; p99 1.5–4.7 s at 50–200 connections [V] |
| `unshare -rn` works on this kernel | yes [V] (will not by default in Docker or on Ubuntu 24.04) |
| Repo visibility | **public** (GitHub API) [V] |

### 0.2 Delta since the previous audit (`cbb577a` → `33a3b3d`, +2,337/-73 lines)
New: `services/toolbox/*` (owner-approved installs of PHP, WordPress, MariaDB, Node, Composer), `services/agents/wordpress.py` + `wp_harness.py` (WordPress missions run PHP/WP-CLI inside the sandbox), `.php` added to sandbox-writable suffixes, `scripts/wsl/hood-pkg` + `enable_installs.sh` (installs a **passwordless-sudo root helper** with a package allowlist), +15 tests. **Not changed:** `ui/server.py:176` init guard, `~/.hood/nova21` path, `docker-compose.yml`, `Dockerfile`, backup script, CI — so earlier blockers B1, B3, B4 **still reproduce at the new head** [V by diff + grep].

---

## 1. Independent verification — where I disagree with the proposal

| # | Proposal says | Finding | Class |
|---|---|---|---|
| 1 | CX43 €15.99, CX23 €5.49 | **Prices are correct** (Hetzner price-adjustment page, effective 2026-06-15, excl. IPv4 and VAT) | V |
| 2 | Plans purchasable | **Not confirmed; CX likely not.** Hetzner pages mark *all* types (CX, CAX, CPX) "currently unavailable"; the tracker (2026-10-05) says CX* sold out (CX23 "Limited" in FSN1/HEL1) but CAX/CPX available — sources conflict for CAX/CPX. Third-party articles quote CX23 €5.99 / CX43 €16.49 — these are the same prices **+ €0.50 IPv4**, not a conflict | V (page) / D (tracker) |
| 3 | One IPv4 €0.50 | **Two VMs ⇒ two IPv4 = €1.00**, unless VM2 is IPv6-only behind a NAT/proxy on VM1 (extra complexity). Primary IPs are billed even when unassigned; powered-off servers are still billed | V |
| 4 | Backup ≈ €3.20 | Correct for CX43 (20 % × €15.99 = €3.20); 7 slots, same-location, **not off-site**. Would be €2.10 on a CAX21 | V |
| 5 | Off-site €5–10 | **Over-estimated.** Hood's data is small (SQLite + ZIP artifacts). Backblaze B2: $6.95/TB-month usage-based, first 10 GB free, no minimum storage fee → < €1 for ≤ 100 GB. Hetzner Object Storage ≈ €4.99 flat incl. 1 TB (price only from third-party sources) | V (B2) / D (Hetzner) |
| 6 | Private access + monitoring €0 | Tailscale free plan is **"only suitable for non-commercial use"**; Standard is $8/user/month. Grafana Cloud free (10k series, 50 GB logs, 14-day retention) and Healthchecks.io free (20 checks) are sufficient for staging; Grafana's free-tier commercial terms are not stated on the page | V |
| 7 | CX23 (2 vCPU/4 GB) may run Chromium + agents | **Memory: yes for 1 mission + 1 browser** (≈ 0.64 GB browser + 0.1–0.35 GB children). **Unmeasured:** PHP/WordPress/MariaDB (not installable here). **CPU: shared 2 vCPU is the limiter** under parallel missions. 8 GB gives headroom for WordPress + a second browser | V / A |
| 8 | VM2 handles browser automation | `BrowserService` launches Chromium **inside the Hood process** (`services/browser/browser_service.py:88-104`). Moving it to VM2 needs a remote Playwright/CDP connection — small but real code | V |
| 9 | VM2 "no secrets" | Feasible: model calls happen in the router on core; the sandbox only runs engine-built argv with a scrubbed env [V]. But the **toolbox's sudo helper and tool installs** must not exist on VM2 — the toolchain must be baked into its image | V / R |
| 10 | Debian 12 vs Ubuntu | See §4.6 | R |
| 11 | "Task queue" on core | There is **no queue**; the engine uses SQLite task leases with fencing tokens [V]. Adequate for one core; do **not** add Redis/Celery | V |

---

## 2. Verified hosting offers (checked 2026-10-10 UTC; availability is volatile)

Direct "Create/Order" links are generated by each vendor's JavaScript configurator and **could not be extracted**; the links below are the official product/pricing pages I read. Verify stock in the console at order time. **Do not treat catalogue price as inventory.**

| Offer | Exact spec | Location(s) | Price (excl. VAT; IPv4 shown separately) | Availability evidence | Fit to measured need |
|---|---|---|---|---|---|
| **Hetzner CX23** [page](https://www.hetzner.com/cloud/cost-optimized/) | 2 shared vCPU (Intel/AMD), 4 GB, 40 GB NVMe, 20 TB | NBG1, HEL1 (FSN1 per tracker) | €5.49 + €0.50 IPv4 | Page "not available"; tracker NBG1 sold out, FSN1/HEL1 "limited" (2026-10-05) | Min. executor, if it appears |
| **Hetzner CX33** | 4 / 8 GB / 80 GB | NBG1, HEL1 | €8.49 + €0.50 | Page "not available"; tracker sold out | Core or executor |
| **Hetzner CX43** | 8 / 16 GB / 160 GB | NBG1, HEL1 | €15.99 + €0.50 | Page "not available"; tracker sold out | Oversized for core (§4.3) |
| **Hetzner CAX11** (Arm Ampere) | 2 vCPU, 4 GB, 40 GB, 20 TB | NBG1, FSN1, HEL1 | €5.99 + €0.50 | Tracker: **Available** (2026-10-05) [D]; ⚠ Hetzner's page says "not available" — **conflict, unresolved** | Min. VM; needs Arm validation |
| **Hetzner CAX21** | 4 vCPU, 8 GB, 80 GB | same | €10.49 + €0.50 | Tracker: Available [D] | **Recommended core and executor** |
| **Hetzner CAX31** | 8 vCPU, 16 GB, 160 GB | same | €20.99 + €0.50 | Tracker: Available [D] | Production core |
| **Hetzner CPX22** [page](https://www.hetzner.com/cloud/regular-performance/) (x86 AMD) | 2 vCPU, 4 GB, 80 GB | NBG1, FSN1 (HEL1 limited) | €19.49 + €0.50 (list); rose from €7.99 on 2026-06-15 | Tracker: Available | x86 fallback; expensive |
| **Hetzner CPX32 / CPX42** | 4/8 GB/160 GB · 8/16 GB/320 GB | NBG1, FSN1 | €35.49 · €69.49 | Tracker: Available | Fallback only |
| **Hetzner CCX13** (dedicated vCPU) | 2 vCPU / 8 GB | all incl. US/SIN | €42.99 | Tracker: Available | Not cost-justified for staging |
| **OVHcloud VPS-2** [page](https://www.ovhcloud.com/en-ie/vps/) | 4 vCore, 8 GB, 75 GB NVMe, 1 Gbps, unlimited traffic, daily backup (previous 24 h only) incl., IPv4 incl. | not stated | **"from" €7.21** (order link uses a **12-month upfront** option; monthly-billing price **not shown**) | Not verified; KYC/stock unknown | Best raw value; unverified terms |
| **OVHcloud VPS-3 / VPS-4** | 6/12 GB/100 GB · 8/24 GB/200 GB | not stated | "from" €10.40 · €19.96 | Not verified | VPS-4 fits core+browser comfortably |
| **DigitalOcean Basic** [page](https://www.digitalocean.com/pricing/droplets) | 2 vCPU/4 GB/80 GB · 4/8 GB/160 GB | 12 regions incl. Frankfurt, Amsterdam, London | $24 · $48 (USD); backups 20 %/30 %; snapshots $0.06/GB | Not stock-checked | 3–5× Hetzner CAX; no advantage here |
| **Scaleway** [page](https://www.scaleway.com/en/pricing/virtual-instances/) | BASIC2-A2C-4G 2 vCPU/4 GB · DEV1-S 2/2 GB | Paris (others not shown) | €16.79 · €6.55 / month (≈ hourly × 730); flexible IPv4 and storage excluded | Not stock-checked | Mid-priced; no advantage here |

**Taxes and billing (Hetzner) [V]:** prices exclude VAT; hourly billing capped at the monthly price; partial hours round up; deleting a server mid-month bills only the hours used; powered-off servers still bill; usage alerts at 75 %/100 % of included traffic are **notifications, not caps**; included traffic ≥ 20 TB in the EU; only outbound traffic is billed. VAT treatment, payment methods and new-customer verification are **not stated** on the pages I could read [open item].
**GPU:** Hetzner GEX45 (RTX PRO 4000 Blackwell, 24 GB) ≈ €214/mo + ≈ €209 setup, GEX44 ≈ €184/mo, GEX131 ≈ €1,197/mo + €599 setup — **all from third-party reporting [D]**; Hetzner's page shows no prices. RunPod on-demand (page, 2026-10-10): RTX 4090 24 GB $0.34–0.89/h, L4 $0.44–0.59, L40S 48 GB $0.79–1.09, A100 80 GB $1.19–1.79, H100 $1.99–3.99; network volume $0.07/GB-month; the page's displayed and structured prices disagree — treat as ±.
**Verdict on the proposal's instances:** specification and price match; **purchasability not confirmed**.

---

## 3. Budget audit

### 3.1 Correct the provisional estimate
| Line | Proposal | Corrected | Why |
|---|---|---|---|
| VM1 | €15.99 (CX43) | CAX21 €10.49 | CX43 not purchasable; measured need ≤ 8 GB |
| VM2 | €5.49 (CX23) | CAX21 €10.49 (CAX11 €5.99 minimum) | Browser + toolchain + headroom |
| IPv4 | €0.50 | €1.00 | One per VM |
| Core backup | €3.20 | €2.10 | 20 % of CAX21 |
| Off-site | €5–10 | €0.50–3 | B2 usage pricing |
| Monitoring/private access | €0 | €0 if non-commercial; +€7.4/user if Tailscale Standard | Licence |
| **Omitted** | — | LLM API spend; VAT; GitHub private-repo minutes; golden-image snapshot; domain (public only); security review (one-off) | See below |

### 3.2 LLM cost per mission — assumption-driven (the repo's evidence records **no token counts**, so this cannot be measured yet [V])
Assumption **[A]**: one mission ≈ 10 model calls × (12k input + 4k output tokens) = 120k in / 40k out. Prices verified today: Gemini 3.8-flash $0.75/$3.75 per M (**doubles to $1.50/$7.50 on 2027-01-01**), 3.5-flash-lite $0.30/$2.50, Claude Haiku 5.5 $0.10/$0.50 (≤ 100k prompt), Sonnet 5.5 $2/$10, Opus 5.5 $4/$20, Fable 5.1 $10/$50; OpenAI gpt-5-mini $0.25/$2 is **third-party only [D]** (OpenAI's page returned 403).
| Model | Per mission (2026) | Note |
|---|---|---|
| Gemini 3.5-flash-lite | **$0.14** | current fast tier |
| Gemini 3.8-flash | **$0.24** (→ $0.48 in 2027) | current standard tier |
| Claude Haiku 5.5 | $0.03 | |
| Claude Sonnet 5.5 | $0.64 | |
| Claude Opus 5.5 | $1.28 | |
| gpt-5-mini | ≈ $0.11 [D] | |
Failure multiplier: the engine allows up to 2 repair rounds and 3 task attempts [V `engine.py:56-57`], so budget **1.5× expected, 3× worst case**.

### 3.3 Scenarios (excl. VAT; add EU VAT, e.g. 19 % in Germany **[A — depends on your country/VAT status]**)

**A. Minimum viable private staging** — 2× CAX11, WireGuard (or Tailscale Personal, non-commercial), free tiers, ~20 missions/month, one mission at a time, WordPress and browser missions not concurrent.
| Item | €/month |
|---|---|
| Core CAX11 | 5.99 |
| Executor CAX11 | 5.99 |
| 2× IPv4 | 1.00 |
| Core backup (20 %) | 1.20 |
| Off-site B2 (< 40 GB) | 0.50 |
| Tailscale/WireGuard, Grafana free, Healthchecks free, GitHub (public repo) | 0 |
| **Infrastructure** | **14.68** |
| LLM (20 missions + chat, 1.5× buffer) | ≈ 7 |
| **Total** | **≈ 22 / month · ≈ 265 / year** |

**B. Recommended daily-use private staging** — 2× CAX21, ~100 missions/month, chat ~1,500 messages, second provider tested, repo **made private**.
| Item | Low €/mo | High €/mo |
|---|---|---|
| Core CAX21 | 10.49 | 10.49 |
| Executor CAX21 | 10.49 | 10.49 |
| 2× IPv4 | 1.00 | 1.00 |
| Core backup (20 %) | 2.10 | 2.10 |
| Golden-image snapshot (≈ 40 GB; per-GB rate **not published on page** [open]) | 0.50 | 0.50 |
| Off-site B2 (≤ 100 GB) | 1.00 | 1.00 |
| Tailscale Standard (1 user) | 0 | 7.40 |
| GitHub Actions minutes over free quota (private repo) | 0 | 5.00 |
| **Infrastructure** | **25.58** | **37.98** |
| LLM (Gemini mix ~$25, chat $3, 2nd provider $5; ×1.5 buffer high) | ≈ 25 | ≈ 45 |
| **Total** | **≈ 51 / month ≈ 607 / year** | **≈ 83 / month ≈ 996 / year** |

**C. Future production, Levels 1+2+3** — keep staging (B-low) and add production.
| Item | Low €/mo | High €/mo |
|---|---|---|
| Staging (B-low infrastructure) | 25.58 | 25.58 |
| Prod core CAX31 | 20.99 | 20.99 |
| Prod executor CAX31 | 20.99 | 20.99 |
| 2× IPv4 | 1.00 | 1.00 |
| Prod core backup (20 %) | 4.20 | 4.20 |
| Snapshot, off-site (≈ 300 GB), domain/DNS (≈ €12/yr [A]) | 4.50 | 4.50 |
| Tailscale Standard ×2 / Grafana Pro ($19) / private CI overage | 0 | 14.7 + 17.5 + 5 |
| **Infrastructure (no GPU)** | **77.26** | **114.46** |
| Level 2 GPU: on-demand 4090-class ≈ 80 h × $0.74 → low; dedicated GEX45 → high | 54 | 214 (+ ≈ 209 one-off setup [D]) |
| Level 3 training: 2 runs × 10 h × L40S $1.09 + 100 GB volume → low; 4 runs + 200 GB → high | 26 | 53 |
| LLM (≈ 500 missions + chat + 2nd provider; 2027 Gemini price doubling not included) | 150 | 300 |
| **Total** | **≈ 308 / month ≈ 3.7 k / year** | **≈ 682 / month ≈ 8.2 k / year** |
| *Subtotal without GPU* | *≈ 227* | *≈ 414* |
One-offs **not in the monthly figures**: independent security review (obtain quotes — I will not guess), GEX setup fee, restore-drill traffic (negligible: 20 TB included).

### 3.4 Hidden or unnecessary charges and controls
- Unassigned Primary IPv4 and powered-off VMs keep billing — delete, don't stop [V].
- Snapshots bill per GB-month; **keep exactly one golden image** and delete old ones.
- Provider usage alerts are not caps: use Hood's reservation ledger + **provider-side spend limits** (Anthropic workspace limits, OpenAI project limits, Google budgets — mechanism to be confirmed per provider [A]).
- **Pricing-file trap:** the shipped price file declares **$0** for all Gemini models. Enabling billing without editing it makes Hood's ledger report $0 while Google bills you. A start-up guard is required (§5, S9).
- GPU pods bill while idle; network volumes bill while stopped.
- OVH "from" prices assume a 12-month prepayment; avoid prepaying during a shortage.
- Optional saving (production): create the executor VM on demand through the Hetzner API (hourly billing); needs an API token on core — only with a project-scoped token and after the remote executor exists [R].

---

## 4. Recommended infrastructure and reasoning

### 4.1 Decision
**Keep two trust zones on two VMs, but sequence the build and re-base the hardware.**
1. **Core VM — "HOOD Core"**: CAX21 (4 vCPU Arm, 8 GB), Debian, Docker. Runs Hood + Caddy only. **Mission execution disabled** (fail-closed) until the remote executor passes its gate.
2. **Executor VM — "Untrusted tier"**: CAX21, Debian, rootless-hostile design: rebuildable from a golden image, **no persistent secrets**, reachable only from core over WireGuard, no direct internet, runs jobs in **gVisor** (`runsc`, systrap platform) containers with a fresh scratch volume per job.
3. **Fallback if Arm fails validation:** CPX22/CPX32 (x86) at 2–3.5× the price; the plan is otherwise identical.
4. **Fallback if the executor slips:** one VM with gVisor containers is acceptable for **staging with no real data and X disabled**; it is **not** acceptable for X or untrusted web content [R].

### 4.2 Why not other designs
| Option | Cost | Isolation | Verdict |
|---|---|---|---|
| Single VM, in-process sandbox (today) | lowest | weakest: children read host files (documented residual risk) | Reject |
| Single VM + gVisor/podman containers | low | good for generated code; same kernel/disk as secrets | Staging fallback only |
| **Two VMs + gVisor on executor** | +€10/mo | hypervisor boundary + syscall boundary; no secrets on executor | **Recommend** |
| Firecracker/Kata microVM | medium | strongest | **Do not depend on it:** needs nested KVM; Hetzner docs do not state nested-virtualisation support [V: absent], and gVisor's own guide warns nested KVM is slow/insecure; revisit on bare metal |
| Ephemeral executor per mission via cloud API | cheapest idle | excellent | Production optimisation (§3.4) |
| Executor on owner's PC/WSL | €0 | weak | Keep only as the owner's explicit "Run on my PC" path |

### 4.3 Is CX23/CAX11 (2 vCPU/4 GB) enough for the executor? **[V measurements, A for untested parts]**
Fits **one** mission + one Chromium (≈ 0.64 GB + ≈ 0.1–0.35 GB per sandboxed test process + OS/Docker ≈ 0.5 GB). It does **not** leave margin for WordPress (PHP + WP-CLI with `memory_limit=512M` + SQLite) plus a browser plus parallel tests, nor for heavier real pages than my test page. **CAX21 (8 GB) is the recommended executor; CAX11 is the minimum.**
**Is CX43 (16 GB) justified for core?** No: peak core measurements are ≤ 0.4 GB for the server and ≤ 1.4 GB for the entire test tree. 8 GB leaves > 5× margin.

### 4.4 Concurrency and scaling [V/R]
Core: single Python process, thread-per-connection, GIL; fine for one owner (p99 degrades beyond ~50 connections). Scale **executors horizontally** (stateless, identical images) and GPUs independently; core stays singular because SQLite and in-memory approvals/X state require one writer [V]. Postgres/queue migration is **not** needed for staging.

### 4.5 Arm64 risk register
| Risk | Status | Mitigation |
|---|---|---|
| Python wheels | **None** — all 39 have aarch64 wheels [V] | — |
| Playwright Chromium on arm64 | Not tested [A: officially supported on Debian/Ubuntu arm64] | Smoke test before purchase commitment: day-1 gate G2 |
| PHP/WordPress/MariaDB/Node arm64 | Distro packages exist [A] | Bake and test in image |
| CI only builds x86 | [V `ci.yml`] | Add `ubuntu-24.04-arm` job + multi-arch image (runner availability/price for private repos: $0.005/min listed; public free [D]) |
| gVisor on arm64 | Supported upstream [A; the guide I read doesn't list architectures] | Verify on the first VM |

### 4.6 Debian 12 vs Ubuntu LTS
| | Debian | Ubuntu 24.04 LTS |
|---|---|---|
| Unprivileged user namespaces (current `unshare -rn` sandbox) | work out of the box [A] | **restricted by AppArmor by default** — breaks `unshare -rn` unless a sysctl is relaxed [A, known behaviour; verify] |
| Support horizon | Debian 12 moved to LTS after the Debian 13 release [A — verify date]; Debian 13 is current stable [A] | standard support to 2029 [A] |
| Footprint, defaults | minimal | slightly heavier (snap, cloud-init) |
| Docker/gVisor/WireGuard/Caddy packages | all first-class | all first-class |
**Recommendation [R]:** **Debian stable (13 if the Hetzner image is offered, else 12 with LTS)** on both VMs, because the current sandbox needs userns and uniformity reduces drift; **choose Ubuntu 24.04 if you want the longest support window, and then run all sandboxes under gVisor (root runtime — no host userns needed) and leave the AppArmor restriction on.** The container base image (`python:3.13-slim-bookworm`) is independent of this choice.

---

## 5. Phase 1 — Security remediation specification

Severity scale: **Critical** (remote compromise), **High**, **Medium**. All "Verified" items were reproduced or read at head `33a3b3d`.

**S1 — ROOT_OWNER takeover behind the reverse proxy — Critical [V reproduced at `cbb577a`; code unchanged]**
- *Root cause:* `ui/server.py:174-177` allows first-run setup when the TCP peer is loopback; Caddy shares the container netns (`docker-compose.yml`), so every internet client is loopback.
- *Design:* remove IP-based trust. First-run setup requires a **one-time bootstrap token** generated on the host by the CLI (printed once, stored hashed, expires in 15 min) or is disabled over HTTP when `HOOD_ALLOWED_HOSTS` is set; initial owner is created via `hood_cli.py init-owner`.
- *Code:* `ui/server.py` init handler; `hood_cli.py` new subcommand; `services/auth/auth_service.py` bootstrap-token table; compose comments.
- *Test:* new `tests/security/test_bootstrap_takeover.py` — request with loopback source + public `Host` and no token ⇒ 403; with valid token ⇒ ok once; expired/replayed token ⇒ 403; **second init always refused**.
- *Verification:* repeat the simulated-proxy request against a staging instance through real Caddy from a non-loopback address.
- *Rollback:* flag `HOOD_BOOTSTRAP_MODE=loopback` (default off in container images) for local dev only.

**S2 — Untrusted client identity: owner lock-out and blind audit — High [V code]**
- *Root cause:* login rate-limit key is `login:{ip}:{username}` with `ip = client_address[0]` (`auth_service.py:373-375`); behind the proxy all clients share `127.0.0.1`, so a remote attacker can lock the owner out by failing logins for the owner's username; recovery limiter `recovery:{ip}` is global; access logs record only 127.0.0.1.
- *Design:* trusted-proxy handling — accept `X-Forwarded-For` **only** from a configured proxy address; key limits on (real IP, username) **plus** a per-IP ceiling and **never** lock the owner out solely by username (use progressive delay + owner-via-VPN allowlist).
- *Code:* `ui/server.py` (`client_address` call sites ~lines 175, 206, 261, 312), `auth_service.py`.
- *Test:* simulated attacker IPs cannot block a login from the owner's IP; spoofed XFF from non-proxy ignored.
- *Rollback:* env flag to restore prior behaviour.

**S3 — Generated code can read host files and secrets — Critical for cloud [V docs + code]**
- *Root cause:* sandbox = `unshare -rn` + rlimits only (`sandbox.py` docstring); same uid, same filesystem.
- *Design:* **remote executor** (T-20): `RemoteWorkspace` implements the same surface as `Workspace` (`apply`, `listing`, `read_files`, `digest`, `run`, `kill_all`, `network_isolated`) over an authenticated channel; the executor service runs each job in a gVisor container with a throw-away volume, no host mounts, no env secrets, no network, rlimits and cgroup limits (memory, pids, CPU).
- *Test:* a job that tries to read `/etc/passwd` host copy, core private-network addresses, metadata IPs, and `/proc` of other jobs must fail; jobs cannot see another job's files; core data never present on executor (checked by filesystem scan in CI).
- *Verification:* red-team script under staging; independent review (G7).
- *Rollback:* mission execution stays disabled (fail-closed) if executor is unavailable.

**S4 — Container privileges — High [V config]**
- *Root cause:* compose suggests `seccomp:unconfined` to make `unshare` work; Dockerfile runs as uid 10001 (good) but no `cap_drop`, `read_only`, `no-new-privileges`.
- *Design:* core container keeps **default seccomp**, `cap_drop: [ALL]`, `read_only: true` + tmpfs, `no-new-privileges`, pids/memory limits, no Docker socket; sandbox-dependent code paths move to the executor, so `unshare` is not needed on core.
- *Test:* container-inspection test in CI asserting those flags; boot test with them applied.
- *Rollback:* compose override file.

**S5 — Egress not enforced — High [V grep: no firewall references in browser, tool_gateway, internet_intelligence, dev_executor]**
- *Design:* **three layers**: (1) cloud firewall: core outbound only to allow-listed provider IP ranges via an **egress proxy on core** (domain allow-list: `generativelanguage.googleapis.com`, second provider, package mirrors); (2) host nftables default-deny outbound; (3) wire the app firewall into browser/tool gateway/internet intelligence. Executor: **no internet**; only WireGuard to core.
- *Test:* from each VM, connections to a non-allow-listed host fail; from a sandbox job, all non-loopback traffic fails.

**S6 — Endpoint exposure — Medium [V code]**
- Reviewed routing: all `/api/*` except `/api/auth/status` and POST `/api/emergency_stop` require session+permission; `/preview/<mission>/<token>/…` is capability-token based with a sandboxing CSP. Exposure risks for a public deployment: unauthenticated `emergency_stop` (can halt service), unauthenticated `status` leaking `initialized`/stop state, tokens in URLs captured by access logs.
- *Design:* staging is private (VPN only) so these are acceptable; for production put them behind IP allowlist/rate limit at Caddy, redact preview tokens from logs, add `HOOD_STOP_REQUIRES_AUTH`.
- *Test:* Caddy config test; log-redaction test.

**S7 — Persistence gaps — High [V code]**
- *Root cause:* audit/memory/vault default to cwd-relative `artifacts/` (→ `/app`, lost on container recreate); `MissionService` uses `Path.home()/".hood"/"nova21"` (`ui/server.py:1431`); toolbox writes `HOOD_DATA_DIR/tools`; evolution/acceleration DBs use `artifacts/`.
- *Design:* single data root. Resolve every store under `HOOD_DATA_DIR`; container sets `HOME=/data/home`; add a **startup invariant**: refuse to start if any registered store path is outside `HOOD_DATA_DIR` in cloud mode.
- *Test:* `tests/preproduction/test_persistence_roots.py` — instantiate runtime with temp data dir, assert all `*.db`/`vault.enc` live under it; **container recreate test** (data survives).
- *Rollback:* migration copies legacy files; keep originals one release.

**S8 — Backup omissions — High [V]**
- *Root cause:* `scripts/hood_backup.py` covers `HOOD_DATA_DIR` only; keys excluded unless `--include-keys` (correct), vault key custody undefined.
- *Design:* after S7 one root ⇒ complete backup; encrypted (age) archive to B2; manifest includes DB schema versions; key escrow procedure (offline copy of `HOOD_VAULT_KEY`/`HOOD_RECEIPT_KEY` separate from data).
- *Test:* backup→restore→run smoke on a **different** host in CI; tamper test (exists); restore-time budget recorded.

**S9 — Credential and pricing configuration — High [V]**
- *Root cause:* compose passes `HOOD_GEMINI_CREDENTIAL` (accepted only as the literal `proxy`), not `GEMINI_API_KEY`; `HOOD_MODEL_PRICING` defaults empty ⇒ all paid calls refused; shipped prices are $0.
- *Design:* secrets via Docker secrets files (`*_FILE` convention) read at start; compose sets `HOOD_MODEL_PRICING=/app/config/model_pricing.json` with **real current prices**; **start-up guard**: refuse to run a paid-tier key against an all-zero price file (cloud mode); per-provider spend cap env.
- *Test:* config-validation tests; price-file schema/as-of staleness check (> 90 days ⇒ warning).

**S10 — Toolbox privilege helper — High (new at `33a3b3d`) [V]**
- `scripts/wsl/enable_installs.sh` installs a root-owned helper and a **passwordless sudo rule** for the Hood user; the allowlist includes `npm`, `composer`, `mariadb-server` (install-time scripts run as root). Not enabled by default (helper absent ⇒ `helper_problem()` returns a hint) — safe **unless someone runs the script on a VM**.
- *Design:* in cloud mode set `HOOD_TOOLBOX_MODE=baked`: the toolbox only *detects* tools baked into the executor image and refuses installs; add a test that the sudo rule/helper are absent on VM images; never run `enable_installs.sh` on VMs.

**S11 — Public repository and secret hygiene — Medium [V]**
- Repository is **public**; vulnerabilities in §5 are visible to attackers. Make it private before staging; keep gitleaks; add push protection; rotate any key ever pasted in a session; no secrets in compose files or GitHub Actions logs.

**S12 — Remaining P0 ledger items — High [D/V]** — F03 (principal propagation), F05 (grant issuer/OS sandbox ⇒ S3), F07 (vault key on Windows — not applicable in Linux cloud, track), F10/F29 (self-development/healing promotion gates — **keep self-development API disabled in staging/production unless owner-approved**), F12 (unify X controller), F23 (Windows desktop — out of cloud scope). Ledger file unchanged at the new head [V].

**X governance in the cloud [R]:** X stays **DORMANT by default**, activation only by ROOT_OWNER via Hood with the existing L4 approval + TTL; add a deployment-level kill switch `HOOD_X_ENABLED=0` (default 0 in staging); X tools only run on the executor tier; target scopes only for networks the executor can reach; **no X on production until independent review**.

### Release acceptance criteria — Gate G1 (security) — all must pass
1. S1, S2, S7, S8, S9 fixed with the regression tests above, green in CI. 2. S4 container flags asserted. 3. S3 executor red-team: zero reads of core data, zero outbound connections. 4. S5 egress tests green on both VMs. 5. Backup→restore on a fresh VM completes and a smoke mission succeeds. 6. gitleaks/pip-audit/SBOM clean. 7. Independent reviewer signs `audit/INDEPENDENT_REVIEW.json`. 8. Owner approves in writing.

---

## 6. Phase 2 — Monitoring, costs and recovery specification

**Principle:** smallest useful stack — no self-hosted Prometheus/Loki/Grafana on the core at staging.

| Need | Tool (cost) | Design |
|---|---|---|
| Uptime & health | Healthchecks.io free (20 checks) + `curl` probe of `/api/auth/status` via VPN from a tiny cron | Check names: `core-up`, `executor-up`, `backup-ok`, `restore-drill`, `cost-reconcile` |
| Backup heartbeats | Healthchecks.io | backup job pings on success/fail; alarm if > 26 h silence |
| Metrics, logs, traces | **Grafana Cloud free** via Grafana Alloy on each VM (10k series, 50 GB logs, 14-day retention) | OTLP from Hood; host metrics |
| Tracing | OpenTelemetry SDK in Hood (none today [V]); spans on `ModelRouter.invoke`, mission lifecycle, executor RPC | attributes: mission_id, provider, model, tokens, usd, level, privacy_class — **never prompt bodies** |
| Log hygiene | existing JSON logger + redactor [V] | redact preview tokens/IDs before shipping; drop request bodies |
| Token & cost accounting | extend the `spend` table | **Gap [V]: columns are only `call_id, mission_id, task_id, reserved_usd, actual_usd, measured, provider, model, simulated, settled` — no token counts.** Add `input_tokens, output_tokens, cached_tokens, latency_ms, status, level, price_version` |
| Per-mission attribution | exists (mission_id) [V]; add `/api/agents/missions/<id>/cost` and daily rollup | |
| Reconciliation | nightly job: provider usage/cost export vs ledger | tolerance ±3 %; alert and **freeze paid calls** on drift. Provider export mechanisms to be confirmed per provider [A] |
| Enforceable limits | in-app reservations (exists [V]) **plus provider-side caps** | daily/monthly caps per provider, per environment key |
| GPU tracking | `nvidia-smi` exporter + pod-hours ledger (`gpu_usage` table: provider, SKU, start/stop, $/h, purpose) | alert on pod running > N hours idle |
| Failure & fallback alerts | alert rules: model 429/5xx rate, fallback used, sandbox unavailable, mission UNVERIFIED rate, emergency stop engaged, X activated, new-IP login, disk > 80 % | notify by email + push |
| Backups | nightly encrypted (age) archive → B2; retention 7 daily / 4 weekly / 3 monthly; plus Hetzner snapshot of core | |
| Restore drills | monthly, scripted, on a throw-away VM; measure RTO/RPO | target RPO ≤ 24 h, RTO ≤ 2 h |
| 24–72 h soak | synthetic mission every 15 min (budget-capped), chat ping, backup + restore, kill-core and kill-executor tests, 30-min network partition, provider outage (stub) | pass: ≥ 99 % success, no leak > 50 MB/h, zero cost-ledger drift, all alerts fire |

---

## 7. Phase 3 — Private staging deployment specification

**Principles:** reproducible, small, private. Nothing is created until you approve spend.

### 7.1 Build and release pipeline
- **Branches:** `main` protected (PR + CI + owner review). Claude and Codex each work in their own **git worktree and branch** (`claude/<task>`, `codex/<task>`), never on `main`, never with production secrets; task-level CODEOWNERS for `ui/server.py`, `services/auth`, `services/agents/sandbox.py`.
- **CI (GitHub Actions):** existing jobs + `arm64` build/test, container flag assertions, compose smoke with real Caddy, persistence/restore test, gitleaks on PRs, pip-audit weekly, SBOM.
- **Images:** multi-arch, built by CI, pushed to GHCR, **pinned by digest** in deploy config; two images: `hood-core`, `hood-executor` (toolchain baked: Python, PHP, WP-CLI, Node, Chromium).
- **Environments:** `dev` (local), `staging` (auto-deploy on merge to `main`, VPN only), `production` (**required reviewer = you**, separate VMs, keys and spend caps; never auto-deployed). No self-approval by any agent.
- **Deploy:** SSH-over-WireGuard from a runner using a short-lived deploy key stored as an Environment secret; script does: pre-deploy backup → pull digest → `compose up` → health → smoke mission → promote or auto-rollback.
- **Migrations:** versioned SQL migrations per SQLite DB (none exist today [V]); backward-compatible; rollback = previous digest + restore if schema changed.
- **Secrets:** per-environment; Docker secrets files on host (root-owned 0400); no `.env` on servers.
- **Config separation:** `config/{dev,staging,production}.yaml` selected by `HOOD_ENV`; prices file per environment; X disabled except dev.
- **Rollback & DR:** previous image digest retained; backup before each deploy; DR = new VM from IaC + latest backup (monthly drill).

### 7.2 Network and ingress
- No public ingress for staging: operator access through **WireGuard** (or Tailscale where its licence fits) to `core` only; Caddy listens on the VPN interface; TLS from the Tailscale name or a private CA.
- Hetzner Cloud Firewall (stateful): core inbound = WireGuard UDP only; executor inbound = none from the internet; private network between VMs (free assumed [A]).
- Production later: Cloudflare/Caddy public ingress with rate limiting, mTLS or SSO in front of Hood; **not before G1–G4**.

### 7.3 Diagram — deployment architecture
```
                         Owner (laptop; Claude/Codex local worktrees)
                              │ git push / PR                │ WireGuard (UDP)
                              ▼                               ▼
                    ┌──────────────────┐         ┌───────────────────────────────────┐
                    │ GitHub           │         │ VM1  HOOD CORE   (Debian, CAX21)   │
                    │  CI · GHCR       │──deploy─►  Caddy (VPN-only)  ──► Hood :8990    │
                    │  Environments    │  (digest)│   │  auth · router · ledger · X   │
                    └──────────────────┘         │   │  SQLite+vault in /data (vol.)  │
                                                  │   ├─► egress proxy (allow-list) ──┼──► Gemini · 2nd provider
                                                  │   └─► node-exporter / Alloy ──────┼──► Grafana Cloud · Healthchecks
                                                  └──────────────┬────────────────────┘
                                          WireGuard / private net │ executor RPC (mTLS)
                                                  ┌──────────────▼────────────────────┐
                                                  │ VM2  EXECUTOR   (Debian, CAX21)    │
                                                  │  executor agent ─► gVisor job ctr  │
                                                  │  (tools baked; no secrets; no net) │
                                                  │  Chromium (CDP over private net)   │
                                                  └───────────────────────────────────┘
        Backups: core ──age-encrypted──► Backblaze B2   ·   Level 2/3 GPU (later) ◄── WireGuard ── core router
```

### 7.4 Diagram — communication flows (mission)
```mermaid
sequenceDiagram
  participant O as Owner (VPN)
  participant C as Core (Caddy+Hood)
  participant P as Providers (Gemini/2nd)
  participant X as Executor (gVisor job)
  O->>C: HTTPS (VPN) create mission → approve plan (hash-bound)
  C->>P: plan / write code (egress proxy, reserved budget)
  P-->>C: proposal (schema-validated)
  C->>X: mTLS RPC: apply files (role write-roots), run fixed argv
  X-->>C: results, digests, logs (no secrets ever sent to X)
  C->>C: verify (deterministic), HMAC receipts, artifact
  C-->>O: status, cost, artifact (SHA-256 verified)
  Note over C,X: X can only reply to core; no inbound from internet; no outbound
```
Allowed flows (everything else denied): Owner→Core 443/VPN · Core→Providers 443 via proxy · Core→Executor mTLS · Executor→Core replies only · Core/Executor→Grafana/Healthchecks 443 (logs scrubbed) · Core→B2 443 (backups).

---

## 8. Future compatibility (preserved, not built in Phases 1–3)
- **Second provider:** keep the router; add adapter + prices + egress host + provider-side caps; independent live test before enabling.
- **Level 1/2 routing:** a routing policy (capability, privacy class, cost, health) inside `ModelRouter`, with decision log; the existing evolution router is **not on the live path** [V] — wire or retire.
- **External GPU:** `LocalProviderAdapter` is loopback-only [V]; add an authenticated *private-node* mode (WireGuard + bearer/mTLS) rather than loosening the rule.
- **Level 3 shadow/training:** shadow calls logged and evaluated against Level 1; training only on rented GPUs, datasets hashed, X_SEALED exclusion test; no trainer exists today [V].
- **Independent CPU/GPU scaling:** executors and GPU nodes are stateless/replaceable; core singular.
- **Claude + Codex concurrency:** isolated worktrees/branches, CI as arbiter, no deployment rights.
- **E-commerce/research/cyber:** each is a new mission profile bound to the executor tier and an egress allow-list; payments and X remain approval-gated (L4).

---

## 9. Implementation backlog (dependencies in brackets)

| ID | Task | Depends | Size* |
|---|---|---|---|
| T-00 | Owner decisions: spend approval, provider choice/caps, repo → private, X disabled in cloud, Arm vs x86 | — | S |
| T-01 | S1 bootstrap-token owner setup + tests | — | M |
| T-02 | S2 trusted-proxy client IP + limiter redesign + tests | T-01 | M |
| T-03 | S7 single data root + startup invariant + container-recreate test | — | M |
| T-04 | S8 backup extension, age encryption, off-host restore test | T-03 | M |
| T-05 | S9 secret-file loader, price file with real prices, zero-price guard | — | S |
| T-06 | S4 hardened compose/Dockerfile + CI flag assertions | T-03 | S |
| T-07 | S10 `HOOD_TOOLBOX_MODE=baked` | — | S |
| T-08 | Multi-arch images + arm64 CI job | — | M |
| T-09 | `HOOD_X_ENABLED` kill switch + tests | — | S |
| T-10 | Spend table token columns + migration + cost API | T-03 | M |
| T-11 | OpenTelemetry spans + metrics endpoint (auth) | T-10 | M |
| T-12 | Alloy/Healthchecks configs, alert rules | T-11 | S |
| T-13 | Reconciliation job + provider-side caps runbook | T-10 | M |
| **G1 gate** | Phase 1 acceptance review | T-01..T-09 | — |
| T-14 | IaC (OpenTofu or scripts) for VPC/firewall/VMs — **dry-run only until approved** | T-00 | M |
| T-15 | Provision core VM, WireGuard, Caddy, deploy digest | G1, T-14, spend OK | M |
| T-16 | Baseline smoke, soak harness, restore drill | T-15 | M |
| T-20 | **Remote executor**: `RemoteWorkspace`, executor agent, mTLS, gVisor jobs, toolchain image | T-06,T-08 | L |
| T-21 | Executor red-team tests + egress tests | T-20 | M |
| T-22 | Remote Playwright/CDP for `BrowserService` | T-20 | S–M |
| T-23 | Provision executor VM, enable Python/web/WordPress missions | T-20,T-21,T-15 | M |
| T-24 | 24–72 h soak, DR drill, rollback drill | T-23 | M |
| T-25 | Independent security review (G7) | T-24 | — |
| T-26 | Second provider adapter + tests | T-10,T-13 | M |
| T-27 | Real routing policy + decision log | T-26 | M |
| T-28+ | Level 2 benchmark, GPU link, Level 3 shadow | T-27 | later |
*S ≈ ≤ 1 day, M ≈ 1–3 days, L ≈ 1–2 weeks of focused work [A].

---

## 10. Automated acceptance tests and release gates

| Gate | Must pass | Evidence |
|---|---|---|
| **G0 Source** | full suite green on Linux x86 **and arm64**; pip-audit, gitleaks, SBOM clean | CI artifacts |
| **G1 Security** (above) | S1–S9 tests, container-flag test, bootstrap/lockout tests | `release/evidence/<sha>/` |
| **G2 Platform** | on the real VM: Chromium smoke, gVisor smoke, WordPress mission, `unshare`/gVisor isolation tests, Arm sanity | staging evidence JSON |
| **G3 Recovery** | backup → restore on fresh VM; recreate-container test; rollback to previous digest | drill logs, RTO/RPO |
| **G4 Cost** | token columns populated; reconciliation within ±3 % on ≥ 20 live missions; provider caps tested by hitting a small cap | ledger vs provider export |
| **G5 Soak** | 24–72 h soak criteria (§6) | soak report |
| **G6 Staging** | `release/staging/STAGING_EVIDENCE.json` produced by the existing gate script | gate output |
| **G7 Independent review** | reviewer sign-off incl. executor escape attempts | `audit/INDEPENDENT_REVIEW.json` |
| **G8 Production** | owner approval in the protected Environment; X remains disabled until separately approved | GitHub approval record |
Tests to add (names indicative): `test_bootstrap_takeover`, `test_trusted_proxy_ip`, `test_owner_not_lockable_remotely`, `test_persistence_roots`, `test_restore_on_fresh_dir`, `test_zero_price_guard`, `test_container_hardening_flags`, `test_executor_cannot_read_core_data`, `test_executor_no_egress`, `test_toolbox_baked_refuses_install`, `test_x_disabled_by_default_in_cloud`, `test_spend_tokens_recorded`, `test_cost_reconciliation_drift_freezes_paid_calls`.

---

## 11. Open items and what I could not verify
- Actual Hetzner purchasability **today** for a new account (order flow requires login; pages show unavailable; tracker is third-party, 5 days old).
- VAT/billing/KYC for new Hetzner customers; OVH monthly (non-prepaid) prices, datacentres, stock and nested-virtualisation policy; Hetzner nested virtualisation (docs silent).
- Arm Chromium, gVisor-on-arm64, PHP/WordPress/MariaDB memory — need a real Arm VM (cost: a few euro-cents of hourly billing for a one-hour test, **only with your approval**).
- LLM token usage per mission (repo evidence has no token counts); OpenAI prices (official page returned 403).
- Provider-side hard spend caps and usage-export APIs per provider.
- Whether the live Gemini journey still passes at `33a3b3d` (not re-run; needs your approval and cap).
- Grafana free-tier commercial terms; Tailscale licence classification of your use.
- CI status for head `33a3b3d`.

---

## 12. Decision

**CONDITIONAL GO**
1. **Now (no spend):** execute Phase 1 (T-00…T-13) and the Phase 2 code (cost/OTel). I recommend starting with T-01, T-03, T-05 — each is small and removes a verified Critical/High defect.
2. **Provision private staging (core VM first) only when:** G1 passes; you approve spend; stock for the chosen type is confirmed in the console at order time (CAX21 preferred, CPX22/CPX32 fallback); the repository is private.
3. **Executor VM only after T-20/T-21.** Until then keep mission execution disabled (static-website missions, which verify by reading files, remain possible).
4. **Production: NO-GO** until G2–G7 and your explicit approval. X stays dormant and disabled in cloud environments until separately authorised after independent review.

*Cheapest meaningful first purchase, if you approve: one CAX21 core VM (≈ €11/month incl. IPv4) after G1 — about half the proposed spend, because the executor VM is not yet usable.*

---

## Appendix — Sources (read 2026-10-10)
Hetzner: [cost-optimized](https://www.hetzner.com/cloud/cost-optimized/) · [regular performance](https://www.hetzner.com/cloud/regular-performance/) · [price adjustment](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/) · [billing FAQ](https://docs.hetzner.com/cloud/billing/faq/) · [primary IPs](https://docs.hetzner.com/cloud/servers/primary-ips/overview/) · [GPU servers](https://www.hetzner.com/dedicated-rootserver/matrix-gpu/) · [object storage](https://www.hetzner.com/storage/object-storage/) · tracker [Hetzner Cloud Radar](https://hetzner.thegoated.dev/) · [shortage article](https://stackvaluelab.com/hetzner-cx-cax-unavailable/) · [GEX45 announcement](https://www.hetzner.com/pressroom/hetzner-expands-its-gpu-portfolio-with-the-gex45/) ·
[OVHcloud VPS](https://www.ovhcloud.com/en-ie/vps/) · [DigitalOcean Droplets](https://www.digitalocean.com/pricing/droplets) · [Scaleway instances](https://www.scaleway.com/en/pricing/virtual-instances/) ·
[Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) · [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) · OpenAI (third-party aggregators only; official page 403) ·
[RunPod](https://www.runpod.io/pricing) · [Backblaze B2](https://www.backblaze.com/cloud-storage/pricing) · [Tailscale](https://tailscale.com/pricing) · [Grafana Cloud](https://grafana.com/pricing/) · [Healthchecks.io](https://healthchecks.io/pricing/) · [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) · [gVisor platforms](https://gvisor.dev/docs/user_guide/platforms/).
Code evidence: `ui/server.py:174-177, 1431, 1464`; `services/auth/auth_service.py:373-375, 616-622`; `services/agents/sandbox.py` (docstring, `Workspace`); `services/agents/engine.py:56-57, 170-181`; `services/browser/browser_service.py:88-104`; `services/toolbox/*`; `scripts/wsl/{hood-pkg,enable_installs.sh}`; `docker-compose.yml`; `Dockerfile`; `scripts/hood_backup.py`; `audit/FINDINGS_F01_F36.csv`.
