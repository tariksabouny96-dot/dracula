# HOOD Governance & Constitutional Architecture

## 1. Constitutional Principles (H01 – H20)

| Rule | Name | Enforcement Mechanism |
| :--- | :--- | :--- |
| **H01** | Zak is Final Authority | Hardcoded authority checks; non-delegable override. |
| **H02** | Silence is Never Approval | `ApprovalService.is_approved()` returns False until explicit signed resolution. |
| **H03** | Stop -> Explain -> Recommend -> Ask | Implemented in `HoodCommander` and `ApprovalService`. |
| **H04** | Grounded Evidence Required | Primary source verification and confidence scoring in `EvidencePacket`. |
| **H05** | Maker-Checker Rule | `DAGOrchestrator._independent_verify()` mandates independent review on L3–L5. |
| **H06** | Zero Autonomous Spend | `FinancialAdvisor.verify_x_financial_boundary()` blocks > \$0.00 spend. |
| **H07** | Backup -> Checkpoint -> Verify -> Rollback | Enforced by `CodeModifier` and `CheckpointTool`. |
| **H08** | Least Privilege Tool Access | Ephemeral `CapabilityGrant` enforced by `ToolGateway`. |
| **H09** | Secret Reference Isolation | Secrets stored only as `SECRET://provider/key` pointers; auto-redacted in logs. |
| **H10** | Path Sandbox Enforcement | Realpath validation prevents traversal outside approved roots. |
| **H11** | Emergency Stop Supremacy | `EmergencyStopController` immediately revokes all grants and halts threads. |
| **H12** | Three Viable Options | `HoodCommander.formulate_three_solutions()` presents balanced alternatives. |
| **H13** | Recommend, Don't Dump | Recommendations include trade-offs, costs, and reversibility ratings. |
| **H14** | Reversibility Classification | Actions explicitly tagged REVERSIBLE, PARTIALLY_REVERSIBLE, or IRREVERSIBLE. |
| **H15** | Bounded Execution | Strict loop termination and timeouts across all DAG schedulers and tools. |
| **H16** | X Invariant (Dormancy) | `XExecutiveController` initializes inactive; requires signed wake token. |
| **H17** | X Rigid Scope | Out-of-scope targets rejected; autonomous scope mutation denied. |
| **H18** | Immutable Audit Trail | Append-only SQLite audit log recording actor, policy decisions, and hashes. |
| **H19** | Governance Immutability | Governance memory can only be mutated with Zak's direct signature. |
| **H20** | Anti-Deception & Truthfulness | Exact capabilities reported; simulated components explicitly tagged. |

---

## 2. Risk Matrix & Action Gates

| Risk Level | Description | Auto-Execution? | Requirements |
| :---: | :--- | :---: | :--- |
| **L0** | Observe, read file, lookup docs | Yes | Safe read capability |
| **L1** | Reversible local test, non-destructive edit | Yes | Valid CapabilityGrant |
| **L2** | Controlled code modification, dev service start | Policy Permitted | Local checkpoint created |
| **L3** | External communication, production deployment | No | Explicit Approval Request + Sign-off |
| **L4** | Financial commitment, credentials, security scan | No | Zak Explicit Approval + Maker-Checker |
| **L5** | Irreversible deletion, production drop, exploit | No | Zak Approval + Snapshot + Second Confirmation |

---

## 3. Approval Workflow Lifecycle

```
[Agent Action Proposed]
         |
         v
[RiskEvaluator.assess_risk()]
         |
         +--> Risk <= L2: Proceed with CapabilityGrant
         |
         +--> Risk >= L3:
                   |
                   v
         [ApprovalService.create_request()]
                   |
                   v
         [Status: PENDING] -------- (Silence / Timeout) -------> [BLOCKED / HALT]
                   |
                   +------ (Zak Approves: True) -------> [Status: APPROVED] -> [Execute Action]
                   |
                   +------ (Zak Rejects: False) -------> [Status: REJECTED] -> [Raise Blocked Error]
```

---

## 4. Emergency Stop Protocol
Triggered via:
- CLI: `hood emergency-stop`
- Voice: "Hood, stop everything"
- Code: `EmergencyStopController.trigger_stop()`

**Actions Taken Instantly**:
1. All active `CapabilityGrant` tokens marked `is_revoked = True`.
2. Browser sessions immediately closed.
3. Supervised subprocesses killed cleanly.
4. Distributed event published to all node cluster members.
5. System state transitioned to `EMERGENCY_STOP`.
