# Hood & X — actual system map (as executed, not as described)

Commit range covered: `faafb93` (imported upstream checkpoint `33c364a`) → this branch.
This map describes code paths that run today. Where a document elsewhere says more, this file wins.

## Runtime flow

```
Browser UI (ui/static, CSP script-src 'self')
   │  cookie session + X-CSRF-Token, Host allowlist (loopback only)
   ▼
ui/server.py  JarvisUIHandler (ThreadingHTTPServer, 127.0.0.1 only)
   │  AuthenticationService: scrypt passwords, SHA-256-hashed session tokens, RBAC
   ├─ /api/chat ───────────► InteractionService ─► HoodCommander (legacy rule/LLM replies)
   │                           └─ ConversationStore (SQLite, per "user:<id>")
   ├─ /api/agents/* ───────► AgentEngine (services/agents)          ◄── the real multi-agent path
   │                           ├─ planner.plan_mission ─► ModelRouter ─► provider adapters
   │                           ├─ specialists (engineer / qa / reviewer) ─► ModelRouter
   │                           ├─ sandbox.Workspace (role write roots, netns, rlimits)
   │                           ├─ verifier.verify (deterministic process checks)
   │                           └─ SQLite: missions, tasks(lease+fence), spend, receipts(HMAC), artifacts, events
   ├─ /api/operations/* ───► MissionService + LocalAgentRuntime (LEGACY local plan demo, no LLM)
   ├─ /api/approvals/* ────► ApprovalService (in-memory, TTL, principal-bound)
   ├─ /api/x/*, sentinel ──► XSessionManager (authority = ROOT_OWNER role via auth service)
   └─ /api/emergency_stop ─► EmergencyStopController
                               ├─ ToolGateway.halt → StopLatch (persisted under HOOD_DATA_DIR)
                               ├─ AgentEngine.halt_all (kill process groups, BLOCK missions)
                               └─ browser / dev_executor / desktop / event bus (reported per subsystem)
```

`hood_cli.py` builds the same runtime (`HoodSystemRuntime`) and exposes `agent …`, `stop`, `stop-reset`.

## Trust boundaries

| # | Boundary | Enforced by | Notes |
|---|----------|-------------|-------|
| T1 | Browser → server | Host allowlist, CSRF token, SameSite=Strict cookie, JSON-only POST, CSP | Emergency stop is reachable without a session (can only stop). |
| T2 | Session → action | `has_permission` per route; engine `owner` column on every query | Chat, memory, approvals, X remain ROOT_OWNER-only. |
| T3 | Model output → engine | Strict pydantic schemas (`extra=forbid`), DAG validation, size caps | Model text is untrusted data; it never becomes argv. |
| T4 | Engine → filesystem | `confine_path`, role write roots, suffix allowlist, no hook files | All-or-nothing batch validation before any write. |
| T5 | Engine → processes | Engine-built argv only, scrubbed env, rlimits, process group, `unshare -rn` | Host filesystem is still readable by children (residual risk). |
| T6 | Engine → providers | ModelRouter: pricing table, atomic reservations, mission budget ledger | Unknown price ⇒ refuse paid call. Mock output refused unless `allow_simulated`. |
| T7 | Evidence → user | HMAC receipts, artifact SHA-256 re-check before download | Receipt key in `HOOD_RECEIPT_KEY` or a 0600 key file. |
| T8 | Operator → X | Approval (L4, TTL) + ROOT_OWNER role; stand-down on stop | X state is in-memory: dormant after restart. |

## Data locations

| Data | Location | Backed up by `scripts/hood_backup.py` |
|------|----------|----------------------------------------|
| Users, sessions (digests), access log | `HOOD_DATA_DIR/auth.db` (legacy: `artifacts/auth.db`) | yes |
| Agent missions, spend, receipts, artifacts, workspaces | `HOOD_DATA_DIR/agents/` | yes (keys only with `--include-keys`) |
| Conversations | `HOOD_DATA_DIR/conversations.sqlite3` | yes |
| Emergency-stop latch | `HOOD_DATA_DIR/emergency_stop.json` | yes |
| Audit events, memory, legacy subsystems | `artifacts/*.db` (cwd-relative, from `hood.config.yaml`) | **no** — back up `artifacts/` separately |
| Vault | `artifacts/vault.enc` + `HOOD_VAULT_KEY` or `artifacts/.vault_key` | vault yes, key: store in OS secret store |

## What is legacy / not on the critical path

`services/core/hood_commander.py` (+ `agents/lead_agents.py`), `services/orchestrator/dag_scheduler.py`,
`services/operations/{agent_runtime,mission_service}.py` predate the agent engine. They remain for
compatibility and are labelled LEGACY in the UI. They must not be cited as multi-agent evidence.
