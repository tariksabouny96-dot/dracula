# HOOD Security & Threat Model

## 1. Threat Assumptions & Perimeter Definition
HOOD is designed to operate securely on a developer's workstation or distributed across private self-hosted nodes while maintaining strict zero-trust boundaries:
1. **Host Isolation**: The host operating system (Windows/Linux) must be protected from accidental modification or rogue code execution.
2. **Untrusted Internet Content**: Web pages, external APIs, and scraped repositories are treated as potentially malicious and parsed using sanitized extractors.
3. **Secret Isolation**: Raw API keys, private certificates, and passwords must never enter prompt contexts, model completion logs, or standard memory tables.
4. **Adversarial Input Defense**: Prompts attempting prompt injection, unauthorized tool grants, financial spending, or emergency stop overrides are refused deterministically.

---

## 2. Core Security Controls

### 2.1 Cryptographic Reference Vault
- **Zero Raw Secrets in Code**: Secrets are stored in `artifacts/vault.enc` encrypted with machine-derived keys or passphrases.
- **Reference Pointers**: Downstream components only handle references in the format `SECRET://<provider>/<key_name>`.
- **Automated Regex Redaction**: All logging and audit outputs pass through `packages/logging/redactor.py`, sanitizing known API key formats (`sk-*`, `AIza*`, Bearer tokens).

### 2.2 Ephemeral Capability Grants
- Agents do not possess ambient tool execution permissions.
- Before executing any tool, the agent must be issued a `CapabilityGrant` with:
  - Specific capability string (`fs:read`, `fs:write`, `desktop:control`).
  - Restricted scope (e.g., relative path within workspace).
  - Explicit expiration timestamp (TTL).
  - Revocable status checked at execution time.

### 2.3 Filesystem Path Sandboxing
- All file operations pass through `ToolGateway.validate_path()`.
- Resolves symlinks and ensures the target path is strictly within `allowed_workspace_roots`.
- Directory traversal attempts (`../`) raise `PermissionDeniedError`.

### 2.4 Human Verification & Anti-Automation Boundary
- Anti-bot mechanisms (CAPTCHA, Cloudflare Turnstile, MFA, 3-D Secure, biometric prompts) are strictly respected.
- Automated bypass or cracking attempts are prohibited.
- `HumanVerificationDetector` automatically flags challenges and transitions the task branch to `WAITING_FOR_HUMAN`, awaiting Zak's completion.

---

## 3. X Offensive Executive Security Model

```
           [X Component: DORMANT BY DEFAULT]
                         |
                         |  (Attempt unauthorized wake)
                         +-----------------------------> [REFUSED: PermissionDeniedError]
                         |
                         |  (Zak signs explicit activation)
                         v
                   [X_ACTIVE State]
                         |
      +------------------+------------------+
      |                                     |
      v                                     v
[Strict Target Whitelist]          [X_SEALED Memory Isolation]
      |                                     |
(Attempt out-of-scope)             (Standard agents cannot query)
      |                                     |
      v                                     v
[REFUSED: SecurityScopeViolation]  [Isolated Storage]
```

### X Invariants:
1. **Permanent Dormancy Until Awakened**: X has zero ambient background activity.
2. **Non-Delegable Owner Authorization**: Only Zak can awaken X.
3. **Rigid Scope Whitelisting**: Offensive tools can only touch targets explicitly defined in an approved `XScopeVersion` YAML manifest.
4. **Scope Immutability**: X cannot modify or expand its own scope.
5. **Sealed Memory Isolation**: Findings, exploit artifacts, and notes are written to `X_SEALED` memory, completely hidden from normal Hood sessions.
