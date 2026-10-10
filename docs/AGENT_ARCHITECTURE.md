# HOOD Multi-Agent Architecture & Inter-Agent Bus

## 1. Executive Structure & Domain Leads

HOOD organizes autonomous capabilities into six specialized domain lead agents operating under the direction of **HoodCommander**:

```
                              +--------------------+
                              |   HoodCommander    |
                              |  (Executive Lead)  |
                              +---------+----------+
                                        |
      +---------------+-----------------+----------------+---------------+
      |               |                 |                |               |
      v               v                 v                v               v
[Engineering]   [Cybersecurity]   [Operations]     [Research]       [Product]
    Lead             Lead             Lead            Lead             Lead
```

### Domain Leads Responsibilities:
1. **Engineering_Lead**: Architecture design, software engineering, integration, refactoring, and automated testing.
2. **Cybersecurity_Lead**: AppSec reviews, vulnerability triage, sandboxing audits, credential isolation, and defense analysis.
3. **Operations_Lead**: Infrastructure deployment, cluster state monitoring, service health, and backups.
4. **Research_Lead**: Technical documentation lookup, paper synthesis, algorithmic discovery, and market research.
5. **Product_Lead**: Feature specification, requirement deconstruction, user experience analysis, and acceptance criteria.
6. **Finance_Lead**: Cost accounting, price tracking, ROI evaluation, and enforcement of zero-spend policy.

---

## 2. Shared Context Bus (`SharedContextBus`)

The `SharedContextBus` provides typed, asynchronous-ready communication between domain leads and specialist workers.

### Packet Model (`ContextPacket`):
- `packet_id`: Unique UUID.
- `packet_type`: `TASK_DELEGATION`, `TASK_RESULT`, `EVIDENCE_SHARE`, `CHALLENGE_REQUEST`, `DISPUTE_RAISED`, `DISPUTE_RESOLVED`.
- `task_id` & `parent_task_id`: Hierarchical correlation tracking.
- `sender` & `recipient`: Targeted routing or wildcard broadcast (`*`).
- `claim` & `evidence`: List of `EvidencePacket` objects backing the statement.
- `confidence`: Confidence score (0.0 to 1.0).
- `evidence_quality`:
  - `VERIFIED_PRIMARY`: Direct observation / primary API / verified file hash.
  - `REPRODUCIBLE_TEST`: Automated test run / compiler output.
  - `DERIVED_INFERENCE`: AI reasoning over grounded data.
  - `UNVERIFIED_CLAIM`: Heuristic / conjecture.
- `risk_level`: L0 to L5 classification.
- `payload`: Structured task-specific metadata.

---

## 3. Dispute Resolution Engine (`DisputeEngine`)

When specialist agents produce conflicting claims or incompatible recommendations, the `DisputeEngine` arbitrates the outcome:

```
[Agent A Claim] <-------- CONFLICT DETECTED --------> [Agent B Claim]
       |                                                     |
       +--------------------------+--------------------------+
                                  |
                                  v
                      [DisputeEngine.raise_dispute()]
                                  |
                                  v
               [Evidence-Weighted Scoring Evaluation]
                                  |
            +---------------------+---------------------+
            |                                           |
    Score Gap >= 0.25                           Score Gap < 0.25
            |                                           |
            v                                           v
[EVIDENCE_WEIGHTED Resolution]                [Independent Judge / Critic]
                                                        |
                                            (If L4/L5 or unresolvable)
                                                        |
                                                        v
                                            [Escalate to Zak (Human)]
```

### Adjudication Strategies:
1. **EVIDENCE_WEIGHTED**: Compares effective quality weights (`VERIFIED_PRIMARY`: 1.0, `REPRODUCIBLE_TEST`: 0.85, `DERIVED_INFERENCE`: 0.60, `UNVERIFIED_CLAIM`: 0.20) multiplied by confidence.
2. **INDEPENDENT_JUDGE**: Enlists Red Team or QA Critic agent for technical arbitration.
3. **CONSERVATIVE_SAFETY**: Selects the path with the smallest blast radius and highest reversibility.
4. **HUMAN_ESCALATION**: Escalates directly to Zak when high-risk boundaries are contested.
