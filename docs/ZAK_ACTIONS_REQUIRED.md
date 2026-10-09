# Required Actions for Zak (System Owner)

## Overview
During the unattended completion run, HOOD was engineered, hardened, and verified under a **strict \$0.00 incremental spend policy**.
All core components are fully implemented and verified with 123 passing automated tests.
To activate live production external services, Zak must complete the following optional configuration steps.

---

## 1. Credentials & API Keys Setup (Optional)
To enable external commercial model providers or specialized APIs:
1. Open terminal and run the secure vault CLI:
   ```powershell
   & .venv\Scripts\python.exe -m packages.auth.vault set gemini api_key "<YOUR_GEMINI_API_KEY>"
   & .venv\Scripts\python.exe -m packages.auth.vault set openai api_key "<YOUR_OPENAI_API_KEY>"
   & .venv\Scripts\python.exe -m packages.auth.vault set anthropic api_key "<YOUR_ANTHROPIC_API_KEY>"
   ```
2. Keys will be stored encrypted in `artifacts/vault.enc` and referenced only via `SECRET://provider/key` pointers.

---

## 2. Review Pending & Past Approvals
Inspect historical audit logs and approval records:
```powershell
& .venv\Scripts\python.exe -m services.audit.service list-recent
```

---

## 3. Local Model Deployment (Ollama / Self-Hosting)
For complete provider-independent offline operation:
1. Download Ollama for Windows or Linux: `https://ollama.ai`
2. Pull recommended local models:
   ```bash
   ollama pull llama3:8b
   ollama pull nomic-embed-text
   ```
3. Set `enabled: true` for provider `local` in `packages/config/settings.py` or runtime config.

---

## 4. X Engagement Scope Authorization
Before activating X for offensive security testing on authorized domains:
1. Create a signed scope YAML manifest (e.g., `artifacts/x_scopes/engagement_001.yaml`):
   ```yaml
   engagement_id: "INTERNAL-AUDIT-2026"
   version: 1
   authorization_confirmed: true
   authorized_by: "Zak"
   in_scope:
     - "localhost:*"
     - "127.0.0.1:*"
   out_of_scope:
     - "production.*"
     - "*.bank.com"
   status: "ACTIVE"
   ```
2. To wake X under supervision:
   ```powershell
   & .venv\Scripts\python.exe -m services.x_control.x_executive wake --scope artifacts/x_scopes/engagement_001.yaml
   ```
