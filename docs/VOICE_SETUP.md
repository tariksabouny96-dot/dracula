# HOOD Voice Calling — Setup, Configuration and Operations

Status of everything below: the **software** is built and tested (see `docs/VOICE_TEST_REPORT.md`). Nothing in this guide has been
run against real LiveKit Cloud, Apple's push service, Gemini Live or a physical iPhone, because no accounts, keys, Mac or phone were
available in the build environment and no paid service may be used without the owner's approval. Steps that need those are marked
**[needs you]**. Labels: **[V]** verified here · **[D]** documented by the vendor/source I read · **[A]** assumption to confirm.

## 1. What you need (nothing is bought for you)

| # | Requirement | Why | Cost (verified 2026-10-10 unless noted) | Needed for |
|---|---|---|---|---|
| 1 | A Hood host that is always on (laptop, mini-PC, VPS) | runs Hood and the voice worker | what you already pay | everything |
| 2 | **LiveKit** project: LiveKit Cloud *Build* (free) or your own `livekit-server` | carries the audio | Build **$0** (1,000 agent-min, 5,000 participant-min, 50 GB, 5 concurrent). *Ship* $50/mo; overage $0.01/agent-min, $0.0005/participant-min **[D]** | calls |
| 3 | **Gemini API key** (Google AI Studio) | the voice model (ears and mouth) | Gemini Live audio in $3.00 / out $12.00 per 1M tokens; free tier exists **[D]**. Free-tier data may be used by Google to improve products: check the terms before private conversations **[A]** | calls |
| 4 | **Apple Developer Program** membership | PushKit/VoIP push, APNs key, TestFlight | USD 99/year **[A: re-check on developer.apple.com]** | Hood ringing the phone; installing beyond 7 days |
| 5 | A **Mac with Xcode ≥ 16.3** | the LiveKit Swift SDK needs Swift tools 6.1 **[V: its Package.swift]** | owner's Mac | building/installing the app |
| 6 | A **physical iPhone** (iOS 16+) | CallKit, PushKit, Face ID/Secure Enclave, real audio route | owner's phone | all device testing (**[B] not done**) |
| 7 | A private path phone → Hood: **Tailscale** (or WireGuard) | Hood's API must NOT be public (open audit items B1–B4) | Tailscale personal plan is free **[A]** | everything |

Without items 4–6 you can still run, test and read everything on the Hood side. Without item 4 the phone can **call** Hood (owner-initiated);
only Hood **ringing** the phone needs Apple push.

## 2. How it is wired (what is public and what is not)

```
iPhone ──(Tailscale, HTTPS)──► Hood host: 127.0.0.1:8990  ← Hood binds to loopback only; a local TLS proxy fronts it
   │                                │  /api/voicecall/*  (device credential)         ▲ voice worker (same host or another)
   └──(WebRTC, token)──► LiveKit ◄──┴──────────────── worker joins the same room ────┘ talks to Hood on a private address
Hood host ──► api.push.apple.com (VoIP push; HTTP/2, token auth)  ·  worker ──► Google (Gemini Live)
```
Only LiveKit (and Apple/Google, outbound from your host) are public. **Never** expose Hood's port to the internet.
Hood accepts only loopback `Host` headers, so a proxy's name must be listed in `HOOD_ALLOWED_HOSTS`.

## 3. Step by step

### 3.1 Install and configure
```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-voice.txt          # only on the machine that runs the worker
cp env.example .env                                       # then edit .env: the voice section is at the bottom
chmod 600 .env
```
Put secrets in files and point at them if you prefer (`LIVEKIT_API_SECRET_FILE=/secure/livekit.secret`, same for the Gemini key and Apple key path).
Never paste a key into a chat, a ticket or a commit; `.env` is git-ignored and Hood never prints secret values.

Minimum to place a call (owner → Hood): `HOOD_VOICECALL_ENABLED=1`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and `GOOGLE_API_KEY` for the worker.
Add the four `APNS_*` values only when you want Hood to ring the phone.

### 3.2 Check it (offline, free)
```bash
python hood_cli.py voicecall doctor
```
Prints `ok / WARN / FAIL` per item: LiveKit URL must be `wss://`, secret lengths, that the `.p8` is an EC P-256 PEM and not world-readable,
bundle id format and the resulting VoIP topic, price-table age, limits. It never contacts any service and never prints a secret. Exit code 1 if anything FAILs. **[V]**

### 3.3 Start Hood and the worker
```bash
python hood_cli.py ui --port 8990                         # Hood (binds 127.0.0.1)
python -m voice_agent.worker start                        # the voice worker (needs the same LIVEKIT_* and GOOGLE_API_KEY)
```
The worker keeps no Hood secret: each call hands it a per-call credential that stops working when the call ends **[V]**.
Health: the worker listens on `:8081` locally. If you run the worker in a sandbox that forces an HTTP proxy, the worker's own websocket must bypass it **[V: this broke registration in the build sandbox until proxy variables were stripped]**.

### 3.4 Let the phone reach Hood privately **[needs you]**
Example with Tailscale (**[A]**; adapt to your setup): install Tailscale on the host and the phone, then on the host
`tailscale serve --bg 8990`, and set `HOOD_ALLOWED_HOSTS=<host>.<tailnet>.ts.net` in `.env`.
Restrict who on your tailnet can reach the host with an ACL so only your phone can.
**Complete Hood's first-run owner setup before putting any proxy in front of it** (a known open audit item trusts loopback during first-run setup).
Use an `https://` address in the app. Plain `http://` is accepted only for private LAN addresses (iOS App Transport Security exception for local networking).

### 3.5 Install the iPhone app and pair it **[needs you]**
Follow `docs/IOS_BUILD_AND_INSTALL.md`. Then, on the Hood host:
```bash
python hood_cli.py voicecall pair       # prints a one-time code, valid 5 minutes
```
In the app enter Hood's address and the code. The phone creates its own Face ID-protected key; Hood stores only the phone's *public* key and a
digest of its credential **[V]**. List/revoke phones with `voicecall devices` / `voicecall revoke <id>`.

### 3.6 Let Hood ring the phone (optional) **[needs you]**
1. Apple Developer → Keys → create a key with *Apple Push Notifications service*; download `AuthKey_XXXXXXXXXX.p8` once. `chmod 600` it.
2. Set `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_KEY_PATH`, `APNS_BUNDLE_ID` (= the app's bundle id; Hood adds `.voip` for the VoIP topic).
3. `APNS_SANDBOX=1` for apps installed from Xcode, `0` for TestFlight/App Store. A phone's tokens belong to exactly one of the two.
4. Re-run `voicecall doctor`, then use the console's test-event route or `POST /api/voicecall/test-event` (owner session) to ring the phone once.

## 4. Spending limits and kill switches
* Caps (`HOOD_VOICECALL_*_USD/MINUTES/MAX_SECONDS`) are enforced **on the server**; the phone's Settings can only lower them. Cost is reserved before a call and
  settled after; at the cap Hood ends the call and falls back to a notification. **[V, simulated usage]**
* Off switches, fastest first: Hood's emergency stop (refuses/ends calls) · `HOOD_VOICECALL_ENABLED=0` and restart · stop the worker · revoke the phone · delete the LiveKit key.
* The monthly numbers in `docs/VOICE_COST_AND_INFRASTRUCTURE.md` are estimates; set the Gemini and LiveKit accounts' own billing alerts too.

## 5. Rotating secrets
| Secret | Rotate by | Effect |
|---|---|---|
| LiveKit API key/secret | create a new key in LiveKit, update `.env` + worker, restart both, delete the old key | live calls drop |
| Per-call agent key | delete `HOOD_DATA_DIR/voicecall/.agent_secret` (or change `HOOD_VOICECALL_AGENT_SECRET`) and restart Hood | live calls end; new ones use the new key |
| APNs key | create a second key in Apple Developer, update `.env`, restart, revoke the old | none |
| A phone's credential | `voicecall revoke <id>`, pair again | that phone stops working immediately |
| Gemini key | new key in AI Studio, update the worker's env, restart | live calls drop |

## 6. Troubleshooting
| Symptom | Likely cause |
|---|---|
| App says "This phone is no longer authorised" | the device was revoked or re-paired: pair again |
| App: "Voice off" | `HOOD_VOICECALL_ENABLED=0`, or LiveKit values missing (`voicecall doctor`) |
| Call connects but Hood never speaks | worker not running or cannot reach `HOOD_API_URL`/LiveKit; check worker logs and `:8081` |
| Hood never rings the phone | no VoIP token registered (open the app once on the phone), wrong `APNS_SANDBOX`, bundle id ≠ app, quiet hours, hourly cap, or the event is not one of the four callable kinds |
| "Voice budget reached" | a daily/monthly cap was hit (Home screen shows the month's spend) |
| Spoken "approve it" does nothing | by design: decisions are confirmed with Face ID on the Confirm tab |
