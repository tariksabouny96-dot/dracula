# Feature module contract (for every new Hood capability)

Follow this exactly so modules can be built in parallel and merged without touching each other.

## Layout
- Code: `services/<module>/` (package with `__init__.py`). HTTP: `services/<module>/api.py`.
- Tests: `tests/<module>/test_*.py` (no `__init__.py`; import as `from tests.<module>.x import ...` only if needed).
- Docs: one section appended to `docs/modules/<module>.md` (what is real, what is simulated, how to configure).
- **Do not edit**: `ui/server.py`, `hood_cli.py`, `ui/static/*`, `ui/routes.py` (except adding one line to
  `API_MODULES`), `requirements.txt`, `services/capabilities/registry.py`, other modules' packages.
  If you need a shared change, describe it in your final report instead.

## HTTP
Register routes in `api.py` with `from ui.routes import route, Raw, RouteConflict, ServiceUnavailable`
(see the docstring in `ui/routes.py`). The server already enforces authentication, CSRF, Host checks and
`permission`. Your handler must:
- scope every read/write by `ctx.user_id` (one tenant never sees another's data — return KeyError → 404);
- require `ctx.require_confirm(...)` for anything with side effects;
- get its service instance via `ctx.service("<module>")`; create the instance lazily in `api.py` with
  `SERVICES.setdefault("<module>", <Service>(data_dir))` where `data_dir = Path(os.environ.get("HOOD_DATA_DIR")
  or Path.home()/".hood") / "<module>"`.
Add your module path (e.g. `"services.exports.api"`) to `API_MODULES` in `ui/routes.py`.

## Capability status
In `api.py` call `services.capabilities.registry.register_state_provider("<capability_id>", fn)` where `fn()`
returns `{"state": ..., "detail": ...}` using only the taxonomy in `registry.STATE_TAXONOMY`.
Never report `verified_online` without a real successful check; unknown is `unknown`.

## Safety rules (enforced by review)
1. External side effects (send email, charge, publish, submit, delete remote data) need an exact-action approval:
   use `services.policy.approval_service.ApprovalService` with `principal`, `options=[{"parameter_sha256": ...}]`,
   TTL, and consume it once. Drafting is free; sending is approved.
2. No secrets in code, logs, test fixtures or HTTP responses. Credentials come from env/vault only.
3. Simulated adapters (local test servers, fakes) must label every record `simulated: true` and the capability
   state `simulated`. A simulated success is never reported as live.
4. Check `packages.security.StopLatch` (pass one in; default `StopLatch()`) before side effects.
5. Path handling uses `packages.security.confine_path`. Untrusted text rendered in UI must be escaped (UI is
   built separately; return plain data, never HTML strings built from user input).
6. Live Gemini (free tier) is available through the proxy: set `HOOD_GEMINI_CREDENTIAL=proxy`,
   `HOOD_MODEL_PRICING=config/model_pricing.free-tier.json`; use `services.model_gateway.router.ModelRouter`
   (never call providers directly) and `gemini-3.5-flash-lite` for tests (bigger models are often 503).
   Live tests get `@pytest.mark.live_provider`; keep them few and short.
7. Tests check observable postconditions (files on disk, rows in DB, HTTP status), include negative and
   cross-tenant cases, and run with `/home/user/dracula/.venv/bin/python -m pytest`.

## Final report (your last message)
List: files added, routes, capability ids and states, what is real vs simulated vs blocked (and why),
test command + result counts, shared changes you need from the integrator, open risks.
