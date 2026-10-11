# Hood iPhone app — build, install and device-test guide

Labels: **[V]** verified by running it (here or in CI) · **[D]** documented by Apple/LiveKit (source read; Apple's developer pages are JavaScript-only, so the
rules below come from my general knowledge and **must be re-checked** on developer.apple.com) · **[A]** assumption · **[B]** blocked: needs a Mac, an Apple
account or a physical iPhone.

## 1. What is verified and what is not

| Claim | Status |
|---|---|
| `ios/HoodVoiceCore` (API client, call state machine, challenge signing, push parser, transcript buffer, event feed) compiles and its 37 tests pass on Linux (Swift 6.0) | **[V]** locally and in CI (`ios / core-linux`) |
| `ios/HoodVoice` (SwiftUI app, CallKit, PushKit, Keychain, Secure Enclave signer, LiveKit client) **compiles on macOS with the real LiveKit Swift SDK 2.17** and its 19 unit tests pass on an **iPhone simulator, Xcode 26.3** | **[V]** in CI (`ios / app-macos`, GitHub-hosted macOS runner) |
| The call controller's decisions (answer fallback to the ring grant, duplicate/busy/unreadable pushes, hang-up fallback, DND-refused ring, server-ended call) | **[V] with fakes** — CallKit, LiveKit and the network are simulated in these tests |
| A **signature made by Swift** verifies in Hood's Python server, and a Python-made vector verifies in Swift | **[V]** (software P-256 key; fixtures in `ios/HoodVoiceCore/Tests/.../Resources`) |
| Secure Enclave key + Face ID per signature | **[B]** not testable without a device. Code path is written to Apple's CryptoKit API but has never run |
| CallKit native incoming-call screen, answer/decline from lock screen | **[B]** |
| PushKit VoIP push delivery and "must report to CallKit" behaviour | **[B]** |
| APNs delivery from Hood (HTTP/2) | **[V]** against a local HTTP/2 server that checks Apple's required headers; **[B]** against Apple |
| Audio route (earpiece/speaker/Bluetooth), interruption by a cellular call, background audio | **[B]** |
| LiveKit media between the phone and a worker | **[V]** Python-client ↔ real `livekit-server` ↔ real worker; **[B]** for the Swift client on a phone |

Nothing here may be reported as "works on iPhone" until the checklist in §6 is completed on a real device.

## 2. Layout
```
ios/HoodVoiceCore/   Swift package, Foundation only: runs on Linux CI and on iOS
ios/HoodVoice/       the app: project.yml (XcodeGen), Sources/{App,Services,Views}, Tests, Config
.github/workflows/ios.yml   Linux core tests + macOS build/test on a simulator (no signing)
```
The Xcode project is **generated** from `project.yml` (never committed): `Info.plist` and the entitlements are generated too.

## 3. Build and run on your own iPhone **[needs you]**
1. Mac with **Xcode 16.3 or newer** (LiveKit's Swift SDK needs Swift tools 6.1; CI uses Xcode 26.3) and `brew install xcodegen`.
2. Apple Developer Program membership (needed for push; a free account cannot enable Push Notifications **[D]**).
3. In `ios/HoodVoice/Config/` copy `Local.xcconfig.example` to `Local.xcconfig` (git-ignored) and set
   `HOOD_BUNDLE_ID = com.yourname.hoodvoice` and `DEVELOPMENT_TEAM = <your 10-character team id>`.
   The same bundle id goes into Hood's `APNS_BUNDLE_ID`; the VoIP topic is `<bundle id>.voip`.
4. In developer.apple.com → Identifiers, make sure that App ID has **Push Notifications** and **Time Sensitive Notifications** enabled **[D]**.
   (VoIP pushes with a token-based `.p8` key need no separate VoIP certificate **[D]**.)
5. ```bash
   cd ios/HoodVoice && xcodegen generate && open HoodVoice.xcodeproj
   ```
   Select your phone, press Run. First launch asks for notifications and (at pairing) the microphone; the first Face ID use asks for permission.
6. On the Hood host run `python hood_cli.py voicecall pair` and enter Hood's address and the code in the app (see `docs/VOICE_SETUP.md`).

Xcode-installed builds register with the **sandbox** APNs environment: keep `APNS_SANDBOX=1` on Hood. The app picks `sandbox`/`production` itself (`#if DEBUG`).
Apps signed with a paid team stay installed for a year; development profiles for free accounts expire after 7 days **[D]**.

### TestFlight
Product → Archive → Distribute → TestFlight (internal testing needs no review) **[D]**. TestFlight builds use the **production** APNs host: set `APNS_SANDBOX=0` on Hood.
Automating this in CI needs your signing certificate and an App Store Connect API key stored as GitHub secrets: **not set up** (it would be a new secret store; ask first).

## 4. How the app behaves (design, for reviewers)
* **Owner calls Hood:** the Call button asks iOS to start a CallKit call first (so the audio session is legitimately activated), then asks Hood for a session, joins the LiveKit room with
  the short-lived token Hood returns, and reports "connected". If iOS refuses the call, **no server session is opened**. **[V with fakes]**
* **Hood calls the owner:** Hood sends a VoIP push (`type=hood.incoming_call`, call id, a 120-second join token). The app parses it strictly, reports it to CallKit **synchronously** (iOS
  punishes VoIP pushes that do not produce a call), and rings. Unreadable or duplicate pushes never become calls; a push while on another Hood call is reported and ended at once. Accept → `answer`
  with Hood; if Hood's API is unreachable at that moment the ring's join token is used. Decline → Hood is told. Nobody answers within 40 s → a **missed call** and Hood sends an
  ordinary notification with a **Call Hood back** action. **[V with fakes]**
* **Reconnection:** if the media link drops the app asks Hood for a fresh token with back-off 1, 2, 4, 8, 15 s and gives up after 60 s, the same limit Hood's watchdog uses. **[V, state machine]**
* **Decisions:** the Confirm tab lists what Hood is waiting on. Approve/Reject asks Hood for a one-time challenge, refuses it if the signed text does not match the shown fields,
  and signs it with the Face ID-protected key. Money, X activation, self-modification and anything unknown are **console-only**. Spoken "yes" never counts.
* **Privacy:** the phone stores only the server address, its device id/secret and its signing key in the Keychain (this device only, after first unlock). Transcripts are in memory only.
  The app's own code logs nothing (the LiveKit SDK has its own logging, which I have not audited).

## 5. Reading the CI result
`.github/workflows/ios.yml` runs on every push that touches `ios/`. The macOS job prints the Xcode version, a warning summary and the test summary. Open the run's log if it fails;
the compile errors appear at the end of the "Build and test" step.
Known, accepted warnings: `allowBluetooth` is deprecated in the iOS 26 SDK (its replacement does not exist in Xcode 16, which we still support).

## 6. Physical-device test checklist **[B] — none of these has been run**
Run in this order; record the result and the date. Use a **sandbox** build first (`APNS_SANDBOX=1`).

| # | Test | Expected |
|---|---|---|
| D1 | Pair with a fresh code | Home shows "Ready"; `voicecall devices` lists the phone with a VoIP token |
| D2 | Press **Call Hood** | CallKit call appears; Hood greets within ~3 s; two-way audio; captions appear |
| D3 | Speak over Hood mid-sentence (barge-in) | Hood stops within ~0.5 s and listens |
| D4 | Lock the phone during a call, wait 2 min | Call continues; lock-screen controls work |
| D5 | Airplane mode 10 s, then off | "Reconnecting…", call resumes without a second ring |
| D6 | Switch Wi-Fi ↔ cellular mid-call | Brief glitch at most; call survives |
| D7 | Switch to AirPods / Bluetooth, then speaker | Audio follows; mute works from the app **and** the system UI |
| D8 | A cellular call arrives during a Hood call | Hood call is held/ended cleanly; no stuck audio |
| D9 | From the console: `POST /api/voicecall/test-event` with the app **killed** | Native incoming-call UI within seconds; Accept connects; Hood speaks the briefing |
| D10 | Same with the phone **locked** and in a **Focus** that allows the app / blocks it | Rings when allowed; when filtered it becomes a missed call + notification |
| D11 | Decline | Hood records "declined"; no notification spam |
| D12 | Ignore for 40 s | "Missed call from Hood" notification with **Call Hood back**; tapping it calls |
| D13 | Say "approve the plan" on a call | Hood refuses and points to the Confirm tab |
| D14 | Approve a mission plan on the Confirm tab | Face ID prompt → plan approved; with a wrong face → nothing applied |
| D15 | Money / X activation items | Shown as console-only; no Approve button |
| D16 | Re-enrol Face ID, then try to confirm | Signing fails (key invalidated by design); re-pair restores it |
| D17 | Revoke the phone in Hood while on a call | Call ends; the app says it is no longer authorised |
| D18 | Reinstall the app | Pairing required again; old credential is useless |
| D19 | 10-minute call | Battery/thermal acceptable; cost on Home matches Hood's `/api/voicecall/costs` |
| D20 | French and (separately) Arabic/Darija conversations per `docs/VOICE_LANGUAGES.md` | Notes recorded |

## 7. Limits of this build
iPhone only, portrait, iOS 16+; no CarPlay, no widgets, no Siri/shortcuts; no console UI for pairing (CLI only); no automatic TestFlight upload.
