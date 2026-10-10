# HOOD Voice Calling — Architecture, Decisions and Acceptance Matrix

Status: **Batch 1 (design)**, updated as batches land. Classification: **[V]** verified by running code/tests here · **[D]** documented by a source I read · **[A]** assumption · **[S]** simulated (fake transport / mock) · **[B]** blocked (needs account, Mac or physical iPhone).

## 1. What already exists in Hood (inspected, reusable)
| Need | Existing component | Reuse |
|---|---|---|
| The brain | `InteractionService.handle_text_input(text, session_id="user:<id>")` (same path as `/api/chat`: grounding, memory, missions offers, approvals, X, emergency stop) | **Call it. No second orchestrator.** |
| Conversation memory | `ConversationStore` (SQLite, per principal) | Shared with text chat ⇒ continuity |
| AuthN/Z | `AuthenticationService` (scrypt, hashed session tokens, RBAC, ROOT_OWNER), `ui/routes.py` feature routes | Reuse; add device and agent credentials for non-cookie clients |
| Missions | `AgentEngine.create_mission / approve_plan` (plan-hash bound approval) | Voice may *draft* plans; approval needs step-up confirmation |
| Approvals | `ApprovalService` (+ `resolve_approval`) | Voice never resolves them directly |
| Money | `CostController.reserve/settle` (daily/monthly/task caps) | Voice minutes reserve/settle against the same caps |
| Egress | `NetworkFirewall.enforce(host, port)` | LiveKit and APNs hosts must be allow-listed |
| Emergency stop | `StopLatch` | Calls refused/ended while engaged |
| Voice scaffolding | `services/voice` (contracts, `VoiceRouter` — Gemini Live marked NOT_IMPLEMENTED, `GeminiVoice` batch STT/TTS with consent) | Reuse enums/consent; new package `services/voicecall` (does not touch the browser voice) |

**Security finding that shapes the design [V, code]:** the text path resolves approvals from a typed `approve <id>` (`interaction_service.py` ~376) and starts the X-activation flow from phrases like "activate x". Forwarding raw speech there would let a spoken or injected utterance bypass step-up confirmation. The voice channel therefore passes `channel="voice"`, which disables those branches **inside InteractionService** (defence in depth), and sensitive actions are confirmed by a device-bound biometric signature (§4).

## 2. Decisions (and the alternatives rejected)
1. **Transport = LiveKit (WebRTC SFU).** Open-source server (a real 1.12.0 binary is used for integration tests here), official Swift SDK, Python Agents framework, provider-neutral for the model. Hidden behind `RealtimeTransport`; only `services/voicecall/livekit.py` and `voice_agent/` know LiveKit. *Rejected:* phone→Gemini Live directly with ephemeral tokens (bypasses Hood governance, locks model); Twilio/PSTN (public phone numbers, out of scope); OpenAI Realtime WebRTC (single-vendor).
2. **One brain.** The speech model is *ears and mouth*; every substantive turn is answered by Hood. Two worker modes behind one config switch:
   - `realtime` (default for feel): Gemini Live (`gemini-3.8-live`) with exactly one tool `ask_hood(utterance)`; instructions force it to read Hood's reply faithfully and never act on its own.
   - `cascade` (most governed): STT → Hood → TTS using Hood's existing cost-tracked, consent-gated Gemini voice.
   The backend `HoodVoiceBridge` is identical for both.
3. **Step-up confirmation via Secure Enclave signatures.** Registered phone holds a P-256 key that requires Face ID/Touch ID. The server issues a one-time challenge bound to (approval or mission plan hash, decision, device, nonce, 60 s expiry); the app signs after biometrics; the server verifies the ECDSA signature before calling Hood's normal approval/plan-approval functions. Spoken "yes" never authorises anything. X activation is not confirmable by voice at all (config flag default off).
4. **Device credentials, not passwords on the phone.** Owner registers a device from an authenticated owner session + explicit confirmation; server returns a 256-bit device secret **once** (stored as SHA-256 digest), the device's public key is stored, push tokens are attached later. Device credentials are valid **only** for `/api/voicecall/*` device routes, never for the general API.
5. **Agent credentials.** The worker authenticates to Hood with a per-call HMAC token (call id, expiry, scope `agent`) derived from a server secret; it can only post turns/usage/heartbeats for that call.
6. **Join tokens.** HS256 LiveKit JWTs minted server-side, ≤ 120 s to connect for incoming calls (≤ 15 min for owner-initiated), identity locked to the device, one room, publish audio only, no admin grants, agent dispatch via `roomConfig.agents` (explicit agent name). LiveKit API secret never leaves the Hood host.
7. **Private by default.** Hood's API is **not** exposed publicly. The phone reaches it over the owner's VPN (Tailscale/WireGuard); LiveKit Cloud (or a self-hosted server) is the only public rendezvous and carries only tokens'-scoped media. The VoIP push carries a short-lived join token so an incoming call can connect even if the phone is momentarily off the VPN. Open blockers B1/B3/B4 from the deployment audit remain; this feature **must not** be used through a publicly exposed Hood.
8. **Cost control is enforced server-side**, not trusted to the worker: reserve worst case at call creation, per-call/daily/monthly caps, a watchdog that deletes the LiveKit room at max duration or cap, worker heartbeats that return `continue:false`.
9. **No raw audio stored by Hood.** LiveKit egress/recording are not used; transcripts follow existing Hood conversation rules. Voice provider data-use terms (free vs paid tier) must be checked by the owner before private conversations [A].

## 3. Component map
```
iPhone app (SwiftUI)                         Hood host (private)                              LiveKit            Provider
 CallKit/PushKit ◄── VoIP push (APNs) ◄────── PushNotificationService ◄─ CallDispatcher ◄─ events
 Home/Call/Settings ── HTTPS (VPN) ─────────► /api/voicecall/* (device auth)
        │  join token                          VoiceSessionManager ─ OwnerCallPolicy ─ CostTracker ─ CallHistory
        └── WebRTC audio ──────────────────────────────────────────────────────────────► room ◄─── voice_agent worker ──► Gemini Live
                                               ▲  HoodVoiceBridge ◄─ /api/voicecall/agent/* (agent HMAC) ───────────────┘
                                               └─► InteractionService.handle_text_input(channel="voice")  (the one Hood)
```
Python modules (`services/voicecall/`): `config`, `contracts`, `store` (calls, events, devices, challenges, usage), `tokens` (HS256/ES256 helpers), `transport` (+ `livekit`, `fake`), `profiles` (provider/model/price registry), `cost`, `policy`, `devices`, `confirm`, `bridge`, `sessions`, `push` (APNs), `dispatcher`, `api` (routes), `wiring`. Worker: `voice_agent/`. iOS: `ios/HoodVoice` (app) + `ios/HoodVoiceCore` (pure Swift package).

## 4. Threat model (voice-specific)
| Threat | Control | Test |
|---|---|---|
| Spoken/injected "approve …", "activate X" | `channel="voice"` branches disabled in InteractionService; bridge never executes model output; approvals only via signed device confirmation; X not voice-confirmable | `test_voice_cannot_approve`, `test_voice_cannot_activate_x` |
| Stolen/unregistered device starts calls | device secret digest + revocation; owner-only; rate limits | `test_device_auth_*` |
| Replay of a confirmation | one-time challenge, expiry, bound to decision+hash+device | `test_confirm_replay_rejected` |
| Token theft | ≤120 s join TTL for pushes, room+identity scoped, no admin grants | `test_token_scope` (+ real LiveKit server) |
| Worker abuse | per-call agent token, scope-limited endpoints, owner principal fixed by the call | `test_agent_token_*` |
| Runaway cost | reserve/settle, caps, watchdog room deletion, heartbeats | `test_cost_cap_*`, `test_watchdog_*` |
| Call spam / harassment | allow-list = owner devices only, frequency caps, dedupe, quiet hours | `test_policy_*` |
| Emergency stop engaged | refuse new calls, end active ones | `test_stop_latch_*` |
| Fake incoming call via ordinary push | only PushKit VoIP push type is used for ringing; ordinary alert push is a *fallback notification* and is labelled as such | `test_push_types` |

## 5. Acceptance matrix (spec §12) — filled in as batches complete
See `docs/VOICE_TEST_REPORT.md` (verified vs simulated vs blocked).
