# Self-learning (`services/learning/`)

HOOD turns experience into durable, trust-ranked lessons on the governed memory
ladder (F25). This is the "learning" module from the original interrupted build.

## Trust ladder
```
OBSERVATION -> CANDIDATE -> PROVISIONAL -> ESTABLISHED
```
- `record_outcome(principal, category, lesson, evidence)` records a lesson. The
  first sighting is an OBSERVATION; the same lesson recurring is reinforced and
  auto-promoted to CANDIDATE (3 sightings) then PROVISIONAL (6).
- HOOD can raise a lesson to CANDIDATE and PROVISIONAL on its own, **but only the
  Root Owner can promote one to ESTABLISHED** — the level HOOD treats as settled
  truth. `establish()` refuses any non-owner. This is the final-authority gate on
  what HOOD "knows".
- `recall(principal, query, min_status)` returns trusted lessons, most-trusted
  first, scoped to the principal (no cross-principal leakage).
- Promotion is forward-only, needs evidence, and is enforced at the memory layer
  too (F25) — a lesson can never silently become trusted.

## Owner HTTP surface
- `GET  /api/learning/lessons` / `GET /api/learning/pending` — review; `pending`
  lists PROVISIONAL lessons awaiting your decision.
- `POST /api/learning/record {category, lesson, evidence, confirm}` —
  CHAT_INTERACTION — record/reinforce.
- `POST /api/learning/lessons/<lesson_id>/establish {confirm}` — OWNERSHIP_ADMIN
  (Root Owner) — promote to settled truth.

## Next wire
The agent engine can call `record_outcome` automatically at mission completion
so HOOD learns from every mission without being asked; the service is ready for
that hook.
