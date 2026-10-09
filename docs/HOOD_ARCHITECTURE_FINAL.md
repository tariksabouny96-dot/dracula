# HOOD System Architecture — Final Autonomous Implementation

## 1. Executive Summary & Vision
HOOD is Zak's provider-independent, hardware-aware, self-hostable autonomous AI Operating System.
It is built strictly according to the **HOOD Master System Specification v1.1** and the **Final Autonomous Build Directives**, guaranteeing:
- **Zero-Cost Operation**: Strict \$0.00 incremental spend policy; fallback to local models or mocks when commercial API keys are absent.
- **Constitutional Governance**: Strict adherence to H01–H20 constitutional principles.
- **Executive Separation**: Dual-agent architecture separating **Hood** (daily operating assistant) and **X** (dormant security offensive executive).
- **Multi-Node Portability**: Cryptographic node identity (Ed25519) and seamless migration between local workstations, cloud instances, and edge devices.
- **Fail-Safe Operation**: Bounded execution, deterministic error handling, and robust emergency stop controls.

---

## 2. Architecture Overview

```
                                +-------------------+
                                |        Zak        |
                                | (Final Authority) |
                                +---------+---------+
                                          |
                      +-------------------+-------------------+
                      |                                       |
                      v                                       v
         +-------------------------+             +-------------------------+
         |          HOOD           |             |            X            |
         |    (Executive OS)       |             |  (Offensive Executive)  |
         |  State: ACTIVE          |             |  State: DORMANT         |
         +------------+------------+             +------------+------------+
                      |                                       |
                      |==== Shared Context Bus / Mediation ===|
                      |
        +-------------+-------------+
        |  Domain Lead Agents (6)   |
        |  - Engineering_Lead       |
        |  - Cybersecurity_Lead     |
        |  - Operations_Lead        |
        |  - Research_Lead          |
        |  - Product_Lead           |
        |  - Finance_Lead           |
        +-------------+-------------+
                      |
        +-------------+-------------+---------------------------------------+
        |                           |                                       |
        v                           v                                       v
+----------------+          +----------------+                     +----------------+
| Model Gateway  |          |  Tool Gateway  |                     | Memory Service |
| (Gemini/Mock/  |          | (Capability    |                     | (Tenant &      |
|  Local Private)|          |  Grants/Paths) |                     |  X_SEALED Sep) |
+----------------+          +----------------+                     +----------------+
        |                           |                                       |
        +---------------------------+---------------------------------------+
                                    |
                                    v
                     +----------------------------+
                     | Immutable Audit Trail (DB) |
                     +----------------------------+
```

---

## 3. Subsystem Breakdown

### 3.1 Core Executive & Orchestrator
- **HoodCommander** (`services/core/hood_commander.py`): Primary task planner and coordinator.
- **DAGOrchestrator** (`services/orchestrator/dag_scheduler.py`): Directed Acyclic Graph scheduler managing dependencies, approval holds, independent Maker-Checker validation, and deadlock-free bounded execution.
- **SharedContextBus** (`services/core/shared_context_bus.py`): Thread-safe inter-agent packet exchange supporting typed communication (task results, evidence sharing, cross-domain queries, and challenge requests).
- **DisputeEngine** (`services/core/dispute_engine.py`): Evidence-weighted arbitration engine resolving disagreements among specialist agents.

### 3.2 Policy, Governance & Emergency Stop
- **Constitutional Engine** (`services/policy/governance.py`): Assesses action risk levels from L0 (read-only) to L5 (destructive/irreversible).
- **ApprovalService** (`services/policy/approval_service.py`): Enforces the rule that **silence is never approval**; manages lifecycle of approval requests.
- **EmergencyStopController** (`services/core/emergency_stop.py`): Instantaneous revocation of tool capability grants, termination of running processes, and broadcast of shutdown state across nodes.

### 3.3 Model Gateway & Cost Accounting
- **ModelRouter** (`services/model_gateway/router.py`): Dynamic, provider-agnostic model routing with automatic fallback from primary providers (Gemini) to deterministic local/mock backends.
- **CostController** (`services/model_gateway/cost_controller.py`): Pre-flight budget enforcement and per-call token accounting ensuring compliance with zero-spend policies.

### 3.4 Tool Gateway & Safe Execution
- **ToolGateway** (`services/tool_gateway/gateway.py`): Capability-token enforcer validating temporary grants and path sandbox boundaries.
- **Development Executor** (`services/dev_executor/`): Code modifier with automatic checkpoint creation, Git branch isolation, and instant rollback.
- **Browser Automation** (`services/browser/`): Headless Playwright integration with domain-allowlisting and screenshot capture.
- **Desktop Control** (`services/desktop/`): Native Win32 desktop interaction with human verification detection and automatic handoff.

### 3.5 Memory & Isolation
- **MemoryService** (`services/memory/service.py`): SQLite-backed persistent memory supporting 10 distinct memory types, temporal validity, and tenant isolation.
- **X_SEALED Memory**: Strictly partitioned storage accessible only when X is authorized and active.

### 3.6 Multi-Node & Evolution Engine
- **NodeManager & ClusterTransport** (`services/nodes/`, `services/transport/`): Ed25519 cryptographic authentication, node discovery, heartbeat sync, and migration bundling.
- **EvolutionEngine & ModelArena** (`services/evolution/`): 3-tier capability tracking, benchmark evaluation, and promotion lifecycle.

---

## 4. Operational Invariants Verified
1. **Financial Invariant**: Zero autonomous financial commitments; strictly \$0.00 spent.
2. **Authority Invariant**: Zak is the sole human authority; approval is never inferred from silence.
3. **Executive Invariant**: X remains dormant until explicitly awakened with Zak's confirmation.
4. **Verification Invariant**: Consequential actions (L3–L5) require independent verification (Maker-Checker).
5. **Sandboxing Invariant**: Operations are strictly constrained to authorized workspace boundaries.
