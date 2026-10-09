# HOOD Cloud Deployment & Migration Blueprint

## 1. Migration Strategy Overview
HOOD is developed locally as a self-contained, portable system. It is designed to migrate effortlessly from the temporary Windows laptop to:
1. **Linux Home Server / Homelab Node** (NUC, mini-PC, Ubuntu Server).
2. **Dedicated Self-Hosted Cloud Node** (Hetzner, OVH, or Oracle Cloud Free Tier).
3. **Multi-Node Hybrid Mesh** (Local Windows desktop + Remote GPU compute node).

---

## 2. Portability Architecture

### 2.1 Storage Decoupling
- **Current**: SQLite database at `artifacts/hood_data.db`.
- **Target**: PostgreSQL 16+ with `pgvector` extension.
- **Migration Path**: `services/nodes/migration.py` exports all tables (memories, audit events, approvals) into portable tar.gz bundles with cryptographic checksums.

### 2.2 Model Provider Independence
- Commercial APIs (Gemini, OpenAI, Anthropic) are completely optional.
- **Self-Hosted Models**: Fully compatible with Ollama, vLLM, and llama.cpp via `LocalProviderAdapter` (`http://localhost:11434/v1`).
- Zero reliance on proprietary cloud services for core routing, memory, or governance.

### 2.3 Distributed Cluster Identity
- Every node generates an Ed25519 public/private keypair at boot.
- Nodes exchange signed heartbeats over HTTPS/gRPC.
- Distributed event bus (`services/transport/node_transport.py`) syncs emergency stop events and cluster state.

---

## 3. Step-by-Step Linux Deployment Guide

### Step 1: Export Local State Bundle
```bash
# On Windows workstation
python -m services.nodes.migration export --output hood_migration_bundle.tar.gz
```

### Step 2: Target Server Setup (Ubuntu 24.04 LTS)
```bash
# Update and install base dependencies
sudo apt update && sudo apt install -y python3.12 python3.12-venv git sqlite3

# Clone repository and create virtual environment
git clone <repository_url> ~/hood
cd ~/hood
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

### Step 3: Import Migration Bundle
```bash
python -m services.nodes.migration import --bundle hood_migration_bundle.tar.gz
```

### Step 4: Systemd Service Configuration
Create `/etc/systemd/system/hood.service`:
```ini
[Unit]
Description=HOOD Autonomous AI Operating System
After=network.target

[Service]
Type=simple
User=hood
WorkingDirectory=/home/hood/hood
ExecStart=/home/hood/hood/.venv/bin/python main.py
Restart=always
RestartSec=5
Environment=HOOD_ENV=production
Environment=PYTHONPATH=.

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hood
```
