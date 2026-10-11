# HOOD Voice Calling — Cost and Infrastructure Requirements

Prices were read from the vendors' own pages on **2026-10-10** unless marked otherwise. **Quoted** = a number printed on the vendor's page. **Estimate** = my arithmetic from quoted
prices and stated assumptions. **Assumption [A]** = not verified. Nothing was purchased and no paid API was called to produce this document: every cost below is arithmetic, none is a measured bill.
The tables are generated from the same price registry the server uses (`services/voicecall/profiles.py`, `minutes_cost_table`), so the document and the code cannot disagree.

## 1. Quoted prices

| Item | Price | Status |
|---|---|---|
| Gemini Live (`gemini-3.8-live`) audio input | $3.00 / 1M tokens | quoted, ai.google.dev/gemini-api/docs/pricing |
| Gemini Live audio output | $12.00 / 1M tokens | quoted |
| Gemini Live text in / out | $0.75 / $4.50 per 1M | quoted |
| Gemini audio token rate | 25 tokens per second of audio | quoted (documentation) |
| Gemini free tier | exists (rate-limited; data-use terms differ from paid) | quoted — **owner must read the terms before private conversations** |
| Gemini 3.8-flash (Hood's reasoning model) | $0.75 in / $3.75 out per 1M; **doubles on 2027-01-01** | quoted |
| Cascade pieces (`gemini-3.5-flash-lite` audio in; `gemini-3.8-flash-tts` audio out) | $0.30 / $9.00 per 1M audio tokens | quoted |
| LiveKit Cloud **Build** | $0/month: 1,000 agent-minutes, 5,000 participant-minutes, 50 GB, 5 concurrent | quoted |
| LiveKit Cloud **Ship** | $50/month; overage $0.01/agent-minute, $0.0005/participant-minute | quoted |
| OpenAI `gpt-realtime` audio in / out | $32 / $64 per 1M tokens | **secondary source only** — OpenAI's pricing page returned HTTP 403; profile is **disabled by default** |
| Apple Developer Program | about USD 99/year | **[A]** re-check; Apple's pages are JavaScript-only and could not be read |
| Tailscale personal | free **[A]** | |

## 2. How a call is priced (assumptions)
* Realtime mode streams the owner's microphone to the model for the whole call: input = 60 audio-seconds per minute (**conservative**; VAD may reduce it).
* The model speaks 50% of the time: output = 30 audio-seconds per minute.
* Hood's own reasoning (`ask_hood`) costs on top in **both** modes: about 4 turns per minute at ≈2,500 input / 120 output tokens each, ≈ $0.0023 per turn, ≈ **$0.0093/min** **[A: depends on memory and prompt size; Hood's cost controller records the real figure]**.
* LiveKit is entered as an allowance of $0.001/min (`HOOD_VOICECALL_LIVEKIT_USD_PER_MIN`). On Build it is $0 up to the free quotas.
* Live-API context is billed cumulatively on long sessions **[D, per the vendor's billing notes]**, so the audio figures are **lower bounds for long calls**; the per-call cap and 15-minute ceiling bound this.

## 3. Monthly cost for 100 / 300 / 600 / 1,000 conversation minutes (USD, estimates)

| Provider | 100 min | 300 min | 600 min | 1,000 min | Per minute |
|---|---:|---:|---:|---:|---:|
| **Gemini Live** (default) — audio + LiveKit allowance | 1.45 | 4.35 | 8.70 | 14.50 | 0.0145 |
| &nbsp;&nbsp;+ Hood reasoning | **2.38** | **7.14** | **14.28** | **23.80** | 0.0238 |
| **Gemini cascade** (STT → Hood → TTS) — audio + LiveKit allowance | 0.82 | 2.46 | 4.92 | 8.20 | 0.0082 |
| &nbsp;&nbsp;+ Hood reasoning | **1.75** | **5.25** | **10.50** | **17.50** | 0.0175 |
| **OpenAI Realtime** (secondary-source prices, off by default) — audio + LiveKit | 9.70 | 29.10 | 58.20 | 97.00 | 0.097 |
| &nbsp;&nbsp;+ Hood reasoning | **10.63** | **31.89** | **63.78** | **106.30** | 0.1063 |

Stress case (owner and model each speaking 80% of the time, Hood's reasoning price doubled after 2027-01-01): Gemini Live 4.21 / 12.63 / 25.26 / 42.10;
cascade 3.36 / 10.07 / 20.13 / 33.55; OpenAI 16.36 / 49.08 / 98.16 / 163.60.
Fixed costs not in the table: Apple Developer Program (≈ USD 99/year **[A]**), your Hood host and VPN (already in place).

**Reading it:** at 300 minutes a month Gemini Live costs about **$7**, the cascade about **$5**, OpenAI Realtime about **$32**. Gemini Live is 4–5× cheaper than OpenAI at the prices I could read.
The cascade is cheapest and the most governed (every word passes through Hood's own text path), at the price of a slower, less natural turn-taking **[A]**.

## 4. Where the money is capped (all enforced server-side, **[V] with simulated usage**)
Reserve-before / settle-after against Hood's `CostController`; per-call, per-day, per-month USD **and** minute caps; a watchdog ends the call at the cap; the phone can only lower limits.
Defaults: $1.00 per call, $3.00/day, $15.00/month, 60 min/day, 300 min/month, 15 min per call, 3 Hood-initiated rings per hour. With the defaults the **worst a month can cost is $15** plus any fixed fees.
A failed or abandoned call is still settled with what it used. Recommended: also set billing alerts on the Gemini and LiveKit accounts, because those are outside Hood's control.

## 5. LiveKit: free tier or self-host?
* **Build ($0)** covers ≈1,000 call-minutes a month if every call-minute used one agent-minute and 1–2 participant-minutes (**[A]**: how LiveKit counts a *self-hosted* worker's participant is not stated on the page I read; the conservative reading above still stays inside Build up to ≈1,000 minutes).
  5 concurrent sessions is far above one owner's needs. Past the quota: Ship $50/month, or overage at $0.01/agent-minute (so 1,000 extra minutes ≈ $10).
* **Self-host** `livekit-server` (open source; a real 1.12.0 binary is what the integration tests here run against **[V]**): $0 software, but it needs a public IP, TLS and open UDP/TCP media ports,
  which is the opposite of Hood's "nothing public" posture. Not recommended until the cloud option is outgrown.
* Because transport sits behind `RealtimeTransport` (`services/voicecall/transport.py`), changing vendor touches `livekit.py` and `voice_agent/` only.

## 6. Infrastructure requirements
| Component | Requirement | Basis |
|---|---|---|
| Hood host | existing; voice adds one SQLite file and one small watchdog thread | **[V]** code |
| Voice worker | Python 3.13; ≈0.5–1 vCPU and ≈0.5–1 GB RAM (Silero VAD model + LiveKit agents) | **[A]** not measured under load |
| Network, Hood host → outside | outbound HTTPS only: the host in `LIVEKIT_URL` and, when APNs is configured, `api.push.apple.com` / `api.sandbox.push.apple.com`; Hood adds exactly these to its egress allowlist when voice is enabled | **[V]** `wiring._seed_firewall` |
| Network, worker → outside | outbound WSS/HTTPS to LiveKit and `generativelanguage.googleapis.com`; this is the *worker host's* own egress, not Hood's firewall | **[V]** worker code |
| Network, phone → Hood | private (Tailscale/WireGuard), TLS | design |
| Phone | iPhone, iOS 16+, microphone, notifications, Face ID/Touch ID | **[V]** project config |
| Inbound ports on the host | **none** | design |
| Concurrency | one active call per owner by design (a second ring becomes an in-call notice) | **[V]** |
| Accounts | LiveKit, Google AI Studio, Apple Developer, Tailscale | see `docs/VOICE_SETUP.md` §1 |

If the host is the 2×CAX21 plan from `HOOD_X_INFRASTRUCTURE_VALIDATION_AND_DEPLOYMENT_SPEC.md`, the worker fits beside Hood on the app node **[A]**; do not put it on the same host as untrusted
sandbox work.

## 7. Provider comparison (what matters for this feature)
| | Gemini Live (realtime) | Gemini cascade | OpenAI Realtime |
|---|---|---|---|
| Price at 300 min (incl. Hood reasoning) | ≈ $7 | ≈ $5 | ≈ $32 (unverified price) |
| Feel (latency, interruptions) | best **[A]** | slower turns | best **[A]** |
| Hood stays the only brain | yes (one tool, `ask_hood`; an integrity check ends the call if the model speaks words Hood did not give it) | yes, structurally (the model only transcribes and reads) | same as Gemini Live |
| Voice-model prompt-injection exposure | model hears raw speech but cannot act | STT output is plain text into Hood's own guarded path | same as Gemini Live |
| Languages | EN/FR enabled; Arabic/Darija **untested** (see `docs/VOICE_LANGUAGES.md`) | same | same |
| Status in this build | **default** | available | **disabled** (price unverified; needs approval and its own key) |

Recommendation: ship with **Gemini Live**, keep the **cascade** one environment variable away as the governed fallback, leave OpenAI off until its price can be read from OpenAI's own page and the owner approves it.
