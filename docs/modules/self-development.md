# Governed self-development (`services/evolution/self_development.py`)

HOOD is meant to keep improving itself — adopt new techniques, optimise and
re-run its own code — while the Root Owner stays the final authority. This
controller is how that happens safely.

## The flow
```
propose  ->  (owner reviews the exact change)  ->  approve  ->  apply (+ rollback)
```
1. **propose(target, new_content, rationale)** — records the change and opens an
   ApprovalService request bound to the content's SHA-256. It is **never written
   to the live tree**. Non-`.py` is stored as-is; `.py` must be syntactically
   valid first.
2. **owner approves** the exact request (hash-bound: an approval for one change
   cannot apply a different one).
3. **apply(proposal_id, is_root_owner=True)** — requires the Root Owner AND the
   matching approval, checkpoints the file, writes it, validates (py_compile plus
   an optional configured test command), and **rolls back on any failure**.

## Inviolable guardrails (constitutional denylist)
HOOD can never propose or apply changes to these — it cannot edit away its own
controls: `services/auth/`, `services/policy/approval_service.py`,
`services/policy/governance.py`, `services/firewall/`,
`services/core/emergency_stop.py`, `packages/security/`, this controller itself,
`scripts/preproduction_gate.py`, `audit/`, `.github/`, `services/x_control/`.
Anything outside the modifiable roots (`services/ packages/ ui/ scripts/ tests/
docs/`) is refused too. An engaged emergency stop blocks propose and apply.

## Owner HTTP surface
- `GET  /api/selfdev/proposals` / `GET /api/selfdev/proposals/<sd_id>` — review.
- `POST /api/selfdev/propose {target_path, content, rationale, confirm}` —
  MODIFY_PROJECT_CODE — records a proposal + approval; writes nothing.
- `POST /api/selfdev/proposals/<sd_id>/apply {confirm}` — OWNERSHIP_ADMIN (Root
  Owner) — applies the approved change with rollback.

## Not done yet
An independent-review gate before promoting larger self-changes, and retiring
the older `self_evolution.py` engine, remain. The current controller is the
safe core: nothing changes HOOD without the owner's explicit, bound approval.
