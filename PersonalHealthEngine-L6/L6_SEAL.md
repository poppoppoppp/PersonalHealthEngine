# Layer 6 Seal

Status: SEALED (CORE)

Date: 2026-08-16

## Scope and result

Layer 6 implements the canonical `L6_CONTRACT.md` AI-reasoning pipeline:

`L3/L4/L5 -> Evidence Bundle -> overall state + hypothesis candidates + base confidence -> ReasoningModelAdapter -> Daily Reasoning -> (medical policy) MedicalModelAdapter -> materialized with provenance`

- Core acceptance: PASS (24/24)
- Full rebuild + deterministic replay: PASS (7/7)
- Semantic equivalence: PASS (15/15)
- Final audit: PASS (20/20)
- Regression suite: PASS (18/18)

Layer states:

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: SEALED
- L5 Health Analytics: SEALED
- L6 AI Reasoning: SEALED (CORE)
- L7 Product Output: NOT STARTED

Canonical boundary declaration:

- L1 = SEALED
- L2 = SEALED
- L3 = SEALED
- L4 = SEALED
- L5 = SEALED
- L6 = SEALED (CORE)

## Important distinction

- `L6 CORE SEALED` = TRUE — the deterministic reasoning architecture, Personal
  Context/Feedback/Pattern loop, model-independent adapters, medical-review policy, and
  provenance/replay are implemented and verified with mock adapters.
- `REAL MODEL INTEGRATION VERIFIED` = FALSE — DeepSeek V4 Pro and MedGemma endpoints are not
  configured; no real paid/local model call is asserted by acceptance or audit.

## Production contract

- Contract: `D:\PersonalHealthEngine-L6\L6_CONTRACT.md`
- Production schema version: 2
- Upstreams (read-only): L3 (schema v8), L4 (schema v2), L5 (schema v2)
- L6 database: `D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3`
- Final audit: `D:\PersonalHealthEngine-L6\L6_FINAL_AUDIT.json`
- Acceptance: `D:\PersonalHealthEngine-L6\L6_ACCEPTANCE.json`
- Rebuild report: `D:\PersonalHealthEngine-L6\full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json`
- Semantic comparison: `D:\PersonalHealthEngine-L6\full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json`

## Sealed production counts

- CURRENT personal context: 2, user feedback: 1
- CURRENT evidence bundles: 1, hypotheses: 1, daily reasoning: 1
- QA sessions: 1, medical reviews: 1, personal patterns: 12 (OBSERVING)
- Model invocations: 1, provenance rows: 256
- Registered definitions: 7, applied migrations: 2

## Core guarantees

- Four information classes strictly separated; AI inference is never promoted to user fact.
- Deterministic Evidence Bundle with SHA-256 and provenance to L3/L4/L5.
- Deterministic overall state (no health score), hypothesis candidates, and base confidence;
  the model may only explain/downgrade confidence, never upgrade it.
- No-look-ahead replay: reasoning for date D uses only data/context <= D.
- Medical-review policy: symptoms / high-risk hypotheses / disease-drug questions trigger a
  reviewer; low-risk stable scenarios bypass it; reviewer unavailable -> UNAVAILABLE (no
  fabricated medical conclusion).
- No diagnosis, no health/readiness/recovery/wellness score, no causal claims, no canonical
  sleep night, missing != zero.
- Model-independent: DeepSeek/MedGemma behind adapters; mock adapters are the only ones
  exercised by acceptance. API keys never persisted; model calls logged as metadata + hashes.
- Personal Patterns are non-causal co-occurrence counts; >=3 confirmations to be ESTABLISHED.

## Known limitations

- Real DeepSeek / MedGemma endpoints are not configured.
- Personal Patterns are co-occurrence counters (non-causal).
- Feedback subject references are soft (non-FK) and may need re-keying after a full rebuild.
- The medical reviewer is a mock.

These are explicit, documented limitations — not hidden defects. Layer 6 does not fabricate
model integration, certainty, diagnosis, or health scores.
