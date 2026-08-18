# Personal Health Engine — Layer 6 to Layer 7 Handoff

## 1. Project and fixed architecture

Personal Health Engine converts continuous health and behavior data into trustworthy,
personalized, explainable insight. The fixed logical architecture is:

1. Layer 1 — Data Acquisition
2. Layer 2 — Raw Health Data Store
3. Layer 3 — Feature Engineering
4. Layer 4 — Personal Baseline
5. Layer 5 — Health Analytics
6. Layer 6 — AI Reasoning
7. Layer 7 — Product Output

Current status:

- L1 = SEALED
- L2 = SEALED
- L3 = SEALED
- L4 = SEALED
- L5 = SEALED
- L6 = SEALED (CORE)
- L7 = NOT STARTED

`REAL MODEL INTEGRATION VERIFIED` = FALSE. The deterministic L6 core is sealed; real
DeepSeek/MedGemma endpoints are not configured and are not asserted.

## 2. Upstream summary (all read-only, all SEALED)

- L3 features: `D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3` (schema v8)
- L4 baselines: `D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3` (schema v2)
- L5 analytics: `D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3` (schema v2)

L6 never modifies these, never re-queries Xiaomi Cloud, never re-parses L2, and never redoes
L3-L5 work.

## 3. Layer 6 architecture and production state

Canonical contract: `D:\PersonalHealthEngine-L6\L6_CONTRACT.md`

Production database: `D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3` (schema v2)

Pipeline:

`L3/L4/L5 -> deterministic Evidence Bundle -> deterministic overall state + hypothesis candidates + base confidence -> ReasoningModelAdapter (mock/DeepSeek) -> Daily Reasoning -> (medical policy) MedicalModelAdapter (mock/MedGemma) -> materialized with provenance`

Personal Context / Feedback / Patterns are the personalization loop, strictly separated by
source class.

## 4. Four information classes (enforced by schema)

- SENSOR / UPSTREAM FACT — L1-L5 (not stored in L6; referenced by provenance).
- USER_REPORTED_CONTEXT — `personal_context.source = 'USER_REPORTED'`.
- AI_INFERENCE — `hypotheses` and `daily_reasoning` (never written back as context).
- USER_FEEDBACK — `user_feedback.source = 'USER_FEEDBACK'`.

A model's inference can never become a user fact: `personal_context` has a CHECK
`source = 'USER_REPORTED'` and hypothesis types are excluded from context.

## 5. Production schema tables

- `schema_migrations`, `definition_registry`, `pipeline_runs`, `processing_checkpoints`, `reasoning_issues`
- `personal_context`, `context_revisions`, `user_feedback`
- `evidence_bundles`, `hypotheses`, `daily_reasoning`, `qa_sessions`
- `model_invocations`, `medical_reviews`, `personal_patterns`, `reasoning_provenance`

## 6. Migrations and definitions

Migrations: `001_foundation.sql`, `002_reasoning_core.sql` (`scripts/apply_migrations_v0_1.py`).

Registered definitions (7): `l6.context.extraction`, `l6.evidence.assembly`, `l6.hypothesis`,
`l6.confidence`, `l6.daily.reasoning`, `l6.medical.review`, `l6.personal.pattern` (all v0.1,
SHA-256 registered).

## 7. Canonical procedures

Daily reasoning (deterministic, mock adapters):

```powershell
python D:\PersonalHealthEngine-L6\scripts\l6_reasoning_materializer_v0_1.py `
  --mode full --l3 ...\personal_health_features.sqlite3 --l4 ...\personal_health_baselines.sqlite3 `
  --l5 ...\personal_health_analytics.sqlite3 --l6 ...\personal_health_reasoning.sqlite3 `
  --reasoning-adapter mock --medical-adapter mock `
  --context ...\l6_context_extraction_v0_1.json --evidence ...\l6_evidence_assembly_v0_1.json `
  --hypothesis ...\l6_hypothesis_v0_1.json --confidence ...\l6_confidence_v0_1.json `
  --daily ...\l6_daily_reasoning_v0_1.json --medical ...\l6_medical_review_v0_1.json `
  --pattern ...\l6_personal_pattern_v0_1.json
```

`--analysis-date YYYY-MM-DD` performs a no-look-ahead replay for a past date. Other entry
points: `l6_context_ingest_v0_1.py` (context + revision), `l6_feedback_v0_1.py` (feedback +
pattern learning), `l6_qa_v0_1.py` (interactive question), `l6_full_rebuild_v0_1.py`
(full rebuild + replay), `l6_acceptance_v0_1.py`, `l6_final_audit_v0_1.py`.

## 8. Acceptance evidence

- Core acceptance: `L6_ACCEPTANCE.json` — PASS 24/24
- Full rebuild + replay: `full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json` — PASS 7/7
- Semantic equivalence: `full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json` — PASS 15/15
- Final audit: `L6_FINAL_AUDIT.json` — PASS 20/20
- Regression suite: `tests\test_l6_reasoning_v0_1.py` — 18/18 PASS

## 9. Sealed production counts

- CURRENT personal context: 2, user feedback: 1
- CURRENT evidence bundles: 1, hypotheses: 1, daily reasoning: 1 (overall NOTABLE_CHANGE, primary SLEEP_DEFICIT, confidence LOW, medical BYPASSED)
- QA sessions: 1, medical reviews: 1, personal patterns: 12 (all OBSERVING)
- Model invocations: 1, provenance rows: 256
- Registered definitions: 7, applied migrations: 2

## 10. Known limitations

- Real DeepSeek / MedGemma endpoints are not configured; acceptance/rebuild use deterministic mock adapters. `REAL MODEL INTEGRATION VERIFIED = FALSE`.
- Personal Patterns are co-occurrence counters (non-causal); >=3 confirmations required for ESTABLISHED.
- Feedback `subject_id` is a soft reference and may need re-keying after a full rebuild.
- The medical reviewer is a mock; no real medical model is asserted.
- Daily reasoning bundle intentionally excludes same-day feedback so replay is a pure function of data + context.

## 11. Layer 7 input contract

Layer 7 (Product Output) consumes the structured `daily_reasoning` (+ its `evidence_bundles`,
`hypotheses`, `medical_reviews`, provenance) and `qa_sessions`, and renders plain-language
output. L6 already provides the "front-end language" (`reasoning_summary`,
`recommended_actions`), so L7 primarily handles presentation, scheduling, notifications, and
the "view evidence" affordance — it must not re-derive reasoning.
