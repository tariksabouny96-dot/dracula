# Operations runbook

## Install (clean machine)

Linux / macOS:
```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium          # or set HOOD_CHROMIUM_EXECUTABLE
python -m pytest -q -rs                        # expect: all pass, live tests skipped as BLOCKED_EXTERNAL
python hood_cli.py ui --port 8999              # binds 127.0.0.1 only
```
Windows (PowerShell):
```powershell
py -3.13 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q -rs
.\.venv\Scripts\python.exe hood_cli.py ui --port 8999
```
On Windows there is no sandbox for agent-written code:
- **Website missions** (HTML/CSS/JS) work anyway: they are verified by reading the files; nothing is run.
- **Python missions** stop before their checks and ask you. Either turn on **Settings › Agents › Run on
  my PC** (each mission then asks you to run its fixed check commands directly on your PC, with no
  isolation), or run HOOD in WSL2, where they get the Linux sandbox: see [WSL2.md](WSL2.md).

First run: open http://127.0.0.1:8999, create the Root Owner, store the one-time recovery key offline.

## Connect the model (needed for live chat, agents and voice)

Without this, Hood starts and the UI works, but every model call is refused.

**In the UI (recommended):** open **Settings › Model provider (Gemini)** as the Root Owner:
1. Paste your Gemini API key and press **Save key**. It is stored encrypted in Hood's vault,
   used immediately (no restart), and never shown again (only its last 4 characters).
2. Under **Prices**, choose **Free tier (no billing)** if your Google project has no billing
   account, or **Enter my prices** (USD per 1,000 tokens). Hood never makes a call it cannot price.
3. Press **Test connection**: one tiny real call; the top bar turns **LIVE · GEMINI**.

To change the key later, paste the new one and **Save key** again. A key or prices saved in
Settings take precedence over `.env`.

**Or with a file:**
```bash
cp env.example .env        # Windows: copy env.example .env
# edit .env: set GEMINI_API_KEY=<your key>; HOOD_MODEL_PRICING is already set
python hood_cli.py status  # expect "gemini: ONLINE"
```
`hood_cli.py` loads `.env` from the repo root on start (real environment variables win).
The shipped `config/model_pricing.free-tier.json` declares $0 for a free-tier key with no
billing account; if billing is enabled on your Google project, put the real prices in it first.

## Voice: use your ElevenLabs voice (optional)

HOOD speaks with Gemini's voice by default. To use a voice you chose on ElevenLabs, open
**Settings › Voice** as the Root Owner:
1. Paste your ElevenLabs API key and press **Save key** (stored encrypted, never shown again;
   HOOD adds `api.elevenlabs.io` to the firewall allow-list as your action).
2. Choose **ElevenLabs (my voice)**, paste your **HOOD voice ID** (ElevenLabs › Voices), and
   optionally an **X voice ID** so X sounds different.
3. Model: `eleven_multilingual_v2` (best quality, many languages) or `eleven_flash_v2_5` (fastest).
4. Price per 1,000 characters: what ElevenLabs charges you (0 if your plan covers it). HOOD
   refuses to speak without a price, like every other paid call.
5. **Save voice settings**, then **▶ Test HOOD voice**.

Listening (speech-to-text) stays on Gemini. Removing the key switches back to Gemini's voice.

## Troubleshooting: "it's not working"

| What you see | Cause | Fix |
|---|---|---|
| `HOOD cannot start: required packages are missing` | requirements not installed in *this* Python | run the `pip install` command it prints (activate the venv first) |
| `pip` error `Cannot uninstall PyYAML ... installed by debian` | installing into the system Python | use the venv from the install steps |
| Windows: `.venv\Scripts\activate` fails with "running scripts is disabled" / "l'exécution de scripts est désactivée" (PSSecurityException) | PowerShell's default execution policy blocks `Activate.ps1` | run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` (this window only), then activate again; or skip activation and call `.\.venv\Scripts\python.exe` directly |
| `status` shows `gemini: CONFIGURED_PENDING_KEY`; top bar `NO AI PROVIDER` | no API key reached Hood | **Settings › Model provider › Save key** (or `GEMINI_API_KEY` in `.env`) |
| Mission `BLOCKED` with "Gemini API key not found"; top bar says `NO AI PROVIDER` | same: Hood started without a key (it reads `.env` only at start) | save the key in **Settings** (no restart needed), then press **Retry blocked work** on the mission |
| Startup says `using .env.txt (rename it to .env)` | Windows Notepad added `.txt` | `ren .env.txt .env` |
| Top bar `AI PRICING NOT SET`; `status` shows `KEY_SET_BUT_NO_PRICING`; "unknown cost" | no price on file for a model Hood calls | **Settings › Model provider › Prices** (or `HOOD_MODEL_PRICING`) |
| Top bar `PROVIDER DEGRADED` | the last AI call failed; the reason is shown in the sidebar and in Settings | fix the cause shown, then **Test connection** |
| Gemini HTTP 404 | a retired model was configured (e.g. `gemini-2.5-flash`) | leave `HOOD_GEMINI_*_MODEL` blank to use the defaults |
| Browser page from another device / `421 Misdirected` | Hood binds 127.0.0.1 and checks the Host header | open it on the same machine at `http://127.0.0.1:<port>`; remote access goes through the Docker + Caddy setup below |
| Python mission `BLOCKED`: "needs your approval to run directly on this PC" | no sandbox on Windows | approve it on the mission page (after turning on **Settings › Agents › Run on my PC**), or run HOOD in WSL2 ([WSL2.md](WSL2.md)); website missions don't need either |
| Website preview: a cart or saved choice resets | the preview runs sandboxed (no storage, no internet) | open `site/index.html` from the mission folder (**Open folder** on the mission page) |
| Voice page: `not_configured` | no key or no price for the voice models (or ElevenLabs key / voice ID / price missing) | set them in **Settings › Model provider** and **Settings › Voice** |
| Voice: "Give consent for cloud audio first" | recordings are only sent to Google after you agree | press **Give consent for cloud audio** on the Voice page |
| Voice: "Microphone permission denied" | the browser blocked the microphone | allow the microphone for `127.0.0.1` in the browser's site settings |
| PowerShell shows `ConnectionAbortedError [WinError 10053]` | (older versions) the browser closed a connection; harmless | update: these are no longer printed |

## Container deployment (Docker)

A production image and reverse-proxy topology ship in the repo (`Dockerfile`,
`docker-compose.yml`, `deploy/Caddyfile`). HOOD still binds `127.0.0.1` inside
its container; Caddy shares that network namespace (`network_mode: service:hood`)
and terminates TLS for the public hostname, so the loopback-only security model
and the loopback-only first-run setup are preserved.

```bash
export HOOD_PUBLIC_HOST=hood.example.com      # the TLS hostname Caddy serves
export HOOD_GEMINI_CREDENTIAL=proxy           # or inject provider keys/pricing
docker compose up -d --build                  # builds hood:latest, starts hood + caddy
# First-run owner setup must come from loopback (do it inside the container):
docker compose exec hood curl -fsS -X POST -H "Host: 127.0.0.1:8990" \
    -H "Content-Type: application/json" http://127.0.0.1:8990/api/auth/init \
    -d '{"username":"zak","display_name":"Zak","password":"<chosen-password>"}'
```

Notes:
- The image healthcheck polls `/api/auth/status` over loopback; `docker ps` shows
  `healthy` once the surface is up. CI builds this image and smoke-tests the
  container on every push (the `docker-build` job).
- Agent missions need unprivileged user+network namespaces (`unshare -rn`).
  Docker's default policy may block them, in which case the sandbox fails closed
  (missions end UNVERIFIED) and everything else runs; enable it by uncommenting
  the `security_opt` block in `docker-compose.yml` on a host you trust.
- Persisted state lives in the `hood-data` volume (`HOOD_DATA_DIR=/data`); back it
  up with `scripts/hood_backup.py` as below.

## Release rehearsal and gate
```bash
python scripts/local_release_rehearsal.py      # clean export, boot, smoke, stop, backup/restore, reboot
python scripts/preproduction_gate.py           # commit-bound evidence in release/evidence/<sha>/; exit 0 only for GO
python scripts/generate_sbom.py                # release/SBOM.cdx.json
```

## Staging deployment (procedure — not yet executed; no staging host was provided)
1. Provision an isolated VM/container with no production data and synthetic accounts only.
2. Deploy the exact gated commit: `git archive <sha> | tar -x`; verify `git rev-parse` matches the gate report.
3. Inject secrets from the secret manager (`HOOD_VAULT_KEY`, `HOOD_RECEIPT_KEY`, provider keys, pricing file).
4. Put a TLS reverse proxy in front if accessed remotely; set `HOOD_ALLOWED_HOSTS=hood.staging.example:443`.
5. Run the rehearsal checks against the staging URL; take a backup; restore it into a second directory and boot it.
6. Record results as `release/staging/STAGING_EVIDENCE.json`:
   `{"commit": "<sha>", "deploy": "PASS", "smoke": "PASS", "backup_restore": "PASS", "rollback": "PASS", ...}`.

## Backup / restore
```bash
python scripts/hood_backup.py backup  --data-dir ~/.hood --out /secure/backups
python scripts/hood_backup.py verify  --archive /secure/backups/hood-backup-<ts>.zip
python scripts/hood_backup.py restore --archive ... --data-dir ~/.hood-restore   # refuses non-empty targets
```
Also copy `artifacts/` (audit, memory, vault) — it is outside `HOOD_DATA_DIR`. Keys go to the OS secret store.

## Rollback
1. Stop Hood (`Ctrl+C`), then `python hood_cli.py stop` if anything is still running.
2. Back up the current data directory (above).
3. Check out the previous gated commit; restore the backup taken *before* the upgrade into an empty directory.
4. Point `HOOD_DATA_DIR` at it, start, run the rehearsal smoke checks.
Schema changes are additive (`CREATE TABLE IF NOT EXISTS`, new columns); the session table now stores
token digests, so all sessions from before this branch are invalid and users log in again.

## Emergency stop
UI STOP button, chat "stop everything", or `python hood_cli.py stop`. The latch persists across restarts.
Release only after investigating: UI (Root Owner, explicit confirmation) or `python hood_cli.py stop-reset --confirm`.
Missions in flight become BLOCKED; inspect `GET /api/agents/missions/<id>/events` before cancelling or re-running.

## Credential rotation
Vault key: `SecretVault(...).rotate_key()` (update `HOOD_VAULT_KEY` if used). Receipt key: rotating invalidates
existing receipts' verification — archive old evidence first. Passwords: change in UI (revokes all sessions).

## Owner Windows-machine handoff (cannot be done in this cloud environment)
Run on the owner's PC with dummy data: full suite, UI login, X wake/stand-down with approval card, emergency
stop while a browser task runs, desktop observe/click with grant, microphone permission states (voice must
report unavailable), Chromium E2E, vault key permission checks. Record results with the commit SHA.
