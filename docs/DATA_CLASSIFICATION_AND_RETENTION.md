# Data classification and retention

| Class | Examples | Where | Protection | Retention / deletion |
|-------|----------|-------|------------|----------------------|
| SECRET | API keys, vault key, receipt key, recovery key | `artifacts/vault.enc`, `HOOD_VAULT_KEY`, `HOOD_RECEIPT_KEY`, key files (0600) | Fernet encryption; key outside ciphertext dir preferred; never logged; scrubbed from sandbox env | Rotate with `SecretVault.rotate_key()`; excluded from backups unless `--include-keys` |
| CREDENTIAL-DERIVED | password hashes (scrypt), session token digests | `auth.db` | One-way hashes only | Sessions expire (12 h) and are revoked on password change / logout |
| PERSONAL | chat history, objectives, memories | `conversations.sqlite3`, `agents/agent_engine.sqlite3`, memory DB | Loopback-only server; per-principal keys for chat and missions | Chat: `POST /api/chat/clear`; missions: delete DB rows + workspace (no API yet) |
| GENERATED CODE | agent workspaces, artifacts | `HOOD_DATA_DIR/agents/workspaces`, `/artifacts` | Owner-scoped download, SHA-256 verified | Kept until operator removes; no automatic expiry yet |
| AUDIT | access logs, audit events, receipts, mission events | `auth.db`, audit DB, engine DB | Receipts HMAC-protected | Kept; no rotation policy yet (open item) |
| X_SEALED | X diagnostic memory | memory DB (`X_SEALED` type) | Excluded from node migration archives | Not exported; deletion by operator |

Rules:
- Model prompts contain the objective and workspace files only — never secrets, other users' data or X_SEALED records.
- Logs and test fixtures must not contain real personal data; tests use synthetic accounts.
- Cloud model calls send objective text to the provider: the UI says "calls model provider" before planning.
