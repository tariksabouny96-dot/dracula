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
```
On Windows the agent sandbox has no network namespace, so agent execution **refuses to run**
(missions end UNVERIFIED). Run agent missions on Linux (or WSL2) until a Windows sandbox exists.

First run: open the UI, create the Root Owner, store the one-time recovery key offline.

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
    http://127.0.0.1:8990/api/auth/init \
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
