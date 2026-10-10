# HOOD NOVA 2.0 — integrated local development release

NOVA 2.0 is a **new interface/integration milestone**, not a fully autonomous operating system, model release, or production security certification.

## Integrated UI

- Keeps the existing cinematic HOOD/X command UI, chat, X governance, missions, Sentinel, economics, intelligence, desktop, memory and authentication.
- Adds a Systems & Integrations tab backed by a read-only `/api/capabilities` endpoint with **26** designed subsystems.
- Each capability indicates one of `ATTACHED_UNVERIFIED`, `MODULE_ONLY`, `PLACEHOLDER`, or `UNAVAILABLE`. The presence of a Python module never implies a live service or successful action.
- Cards link to real existing sections where possible. Incomplete capabilities show informative, non-executable placeholders rather than fake actions.
- Includes category and free-text filters, refresh, readiness totals, and keyboard accessible buttons.
- Adds a Systems launcher to Command Deck and mobile navigation.
- Adds locally stored reduced-motion and compact-card settings; these do not alter security or external provider configuration.
- Replaces numerous hardcoded status assurances and misleading mock-success labels with unverified or unavailable states.
- Keeps chat file attachment disabled rather than suggesting uploads succeed; mode pills for unimplemented tool/file flows are marked previews.

## Backend

- New `services/capabilities/registry.py` exposes an explicit capability inventory.
- `/api/capabilities` requires authentication plus `VIEW_PROJECT_DATA`. It does not return credentials, secrets, raw filesystem paths, or model API keys.
- Runtime attached checks are **shallow** and intentionally conservative; genuine online probes, authenticated feature tests, and persistent per-user capability state are not implemented.
- Runtime telemetry no longer blindly claims computer control is online, voice microphone is connected, or Gemini is live.

## Security boundaries maintained

- Owner permissions on sensitive X operations and approvals are preserved from the previous remediation.
- No X activation, shell execution, desktop actions, browser navigation, provider API calls, deployment, migration, or financial transactions were performed to build NOVA.
- Integration controls in Systems are read-only.

## Testing

- Python compileall and `node --check` pass for modified files.
- NOVA registry regression tests pass (source-based and attachment-state assertions).
- 60 targeted tests passed; one environment bootstrap test was excluded due to sandbox prerequisite differences.
- Local HTTP smoke: HTML/JS/CSS 200, static traversal 404, capability endpoint 503 before auth setup, 401 when unauthenticated with dummy initialized auth, 200 with dummy authenticated project-view permission.
- Windows voice, actual provider availability, UI pixel match, production deployment, auth flows with real accounts and operating system automation **not verified**.

## Windows smoke steps (dummy data only)

1. Back up the original Hood folder and preserve its private `.env`, credentials, vault, and databases outside the replacement source tree.
2. Create a new folder for NOVA; install dependencies into a project-local virtual environment using the documented existing startup procedure.
3. Start Hood on 127.0.0.1 using the repository's UI/CLI procedure.
4. Create/sign in with a **new dummy owner** on a disposable profile. Open Systems; inspect 26 capability cards, filters, refresh, navigation, and browser-only preferences.
5. Exercise safe text chat, permissions, task/status views and pending approvals with dummy inputs. Confirm every incomplete module remains labeled unverified/placeholder.
6. Verify at desktop and mobile widths, check the browser console, and test keyboard focus and reduced-motion preferences.
7. Do not activate X or privileged/real autonomous actions as part of smoke verification.

## Known limitations

See `REMEDIATION_REPORT.md` for the remaining F01–F36 status. NOVA does **not** complete durable multi-agent workers, full context continuity, genuine checker receipts, model L3 inference, real voice hardware, independent security assurance, or external business/service integrations. They must be implemented and independently tested before production.
