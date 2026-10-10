# Egress firewall (`services/firewall`)

A default-deny outbound network policy HOOD consults before any live external
call. It is HOOD's own firewall: nothing leaves the machine to a host the Root
Owner has not explicitly allowed.

## Guarantees
- **Default deny.** `authorize(host, port)` allows a connection only if an owner
  rule matches (exact host, or a single `*.suffix` wildcard) and the port is
  listed. Everything else is denied.
- **Owner authority.** Adding or revoking rules requires the Root Owner; agents
  and the self-development loop may read the policy but can never widen it.
- **Fail closed.** An unreadable policy, an evaluation error, or an engaged
  emergency-stop latch denies all egress.
- **Audited.** Every allow/deny decision is recorded through the audit service.
- **Enforced at the gateway.** `ModelRouter` calls the firewall before any live
  provider request (Gemini, OpenAI); a blocked host means the provider is
  refused before any spend. Loopback is allowed by default so the local UI and
  sandboxed loopback servers keep working.

## Managing rules (Root Owner only, over the authenticated UI/API)
- `GET  /api/firewall/rules` — list rules and confirm the default-deny policy.
- `POST /api/firewall/rules {host, ports, note, confirm}` — add an allow rule.
- `POST /api/firewall/rules/<fw_id>/revoke {confirm}` — remove one.

Enabled providers' hosts (e.g. `generativelanguage.googleapis.com`) are seeded
at startup so a configured system works out of the box; the owner can revoke any
seeded rule to cut a provider off.
