# Personal Health Engine — Layer 3 to Layer 4 Handoff

## 1. Project and fixed architecture

Personal Health Engine converts continuous health and behavior data into versioned, provenance-aware evidence. It is not a “health report plus GPT” system and it is not a Xiaomi wearable add-on.

The fixed logical architecture is:

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
- L4 = NOT STARTED
- L5 = NOT STARTED
- L6 = NOT STARTED
- L7 = NOT STARTED

L3A/L3B/L3C are internal Layer 3 stages, not additional logical layers.

## 2. Upstream summaries

Layer 1 collected Xiaomi near-raw records. It must not be reopened or queried during normal Layer 3/4 processing.

Layer 2 is the immutable, versioned raw source of truth at `D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3`. Layer 3 always opens it read-only and uses `raw_record_observations.id` as the change frontier. Do not modify Layer 2, recollect Xiaomi Cloud, or reinterpret its identity/version model in Layer 4.

## 3. Layer 3 architecture and production state

Canonical contract: `D:\PersonalHealthEngine-L3\L3_CONTRACT.md`

Production database: `D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3`

Schema version: 8

Pipeline:

`L2 -> L3A normalized facts -> L3B evidence quality/source resolution -> L3C derived features`

Authoritative recovery is a full rebuild from L2. Incremental processing is an optimization and is proven semantically equivalent for the implemented scope.

## 4. L3A normalized facts

Every fact is registered in `fact_registry`, stored in exactly one temporal table, and linked through `fact_provenance` to an L2 `(logical_record_id, raw_version_id)` pair.

Temporal models:

- POINT: `normalized_point_facts.event_time_utc`
- DAILY: `normalized_daily_facts.local_date`; no fabricated event timestamp
- BUCKET: `normalized_bucket_facts.bucket_anchor_time_utc`; width remains nullable
- INTERVAL: `normalized_interval_facts.start_time_utc/end_time_utc`; duration may be zero

Evidence types are independent from temporal types:

- `SENSOR_DERIVED`
- `VENDOR_DERIVED`
- `VENDOR_INFERRED`

### Canonical dataset semantics

| L2 dataset | L3 metric | Temporal type | Evidence | Value/unit | CURRENT |
|---|---|---:|---|---|---:|
| heart_rate | heart_rate | POINT | SENSOR_DERIVED | bpm / bpm | 2,614 |
| spo2 | spo2 | POINT | SENSOR_DERIVED | spo2 / percent | 385 |
| stress | xiaomi_stress_score | POINT | VENDOR_DERIVED | vendor score / vendor_score | 483 |
| resting_heart_rate | resting_heart_rate | DAILY | VENDOR_DERIVED | bpm / bpm | 2 |
| steps | steps | BUCKET | VENDOR_DERIVED | steps / steps | 468 |
| calories | calories | BUCKET | VENDOR_DERIVED | vendor calories / vendor_calories | 1,064 |
| sleep | sleep_source_episode | INTERVAL | VENDOR_INFERRED | source episode | 14 |
| sleep | sleep_vendor_stage_segment | INTERVAL | VENDOR_INFERRED | vendor stage / vendor_stage | 149 |

### Sleep special model

One L2 Sleep logical record produces one `sleep_source_episode` plus zero or more `sleep_vendor_stage_segment` facts. It does not represent one canonical night.

Verified Xiaomi stage mappings:

- NUMERIC_SOURCE: 2=DEEP, 3=LIGHT, 4=REM, 5=AWAKE
- XIAOMI_GENERATED: 5=AWAKE, 6=SLEEP

All stages remain `VENDOR_INFERRED`. Two real zero-duration vendor segments are preserved exactly. No negative interval is accepted. No `true_sleep`, physiological stage, or canonical night is produced.

### Steps and calories source rules

`NUMERIC_SOURCE` and `XIAOMI_GENERATED` coexist. They are never directly summed across sources.

Generated steps currently satisfy `embedded calories = steps * 0.04`; this relation is scoped only to the verified generated source. Standalone calories are not globally equivalent to embedded calories. Bucket width and calories physical unit remain unresolved.

## 5. Revision and checkpoint semantics

- NEW: materialize a new CURRENT fact/output.
- REVISION: mark the complete old dependent set STALE and materialize the new set. Sleep revision invalidates the episode and every segment together.
- REOBSERVATION: business no-op; the observation frontier may advance.

Checkpoints are stored in `processing_checkpoints` and are based on L2 `raw_record_observations.id`, never health/event time. A checkpoint advances only in the same successful transaction as its pipeline output.

Definition identity is `(definition_id, definition_version)`. File SHA-256 is registered in `definition_registry`; an existing version with different bytes is rejected.

## 6. L3B quality framework

Tables:

- `quality_assessments`
- `quality_assessment_inputs`

Definition: `l3b.quality.structural` v0.1

Every CURRENT L3A fact has three explicit assessments:

- `STRUCTURAL_VALIDITY`
- `PROVENANCE_COMPLETENESS`
- `VENDOR_SEMANTIC_CERTAINTY`

Results are `PASS`, `FLAGGED`, or `UNKNOWN`. Vendor-derived/inferred semantics are `UNKNOWN`, not “bad data” and not physiological truth. No clinical threshold or opaque score is used.

CURRENT assessments: 15,537.

## 7. L3B source resolution

Tables:

- `source_resolution_decisions`
- `source_resolution_inputs`

Definition: `l3b.resolution.source` v0.1

Resolution groups use metric/component and exact temporal/timezone context. Outcomes preserve all inputs:

- one source class: `SINGLE_SOURCE`
- multiple sources with equal value/unit: `AGREE + COEXIST`
- disagreement: `CONFLICT + UNRESOLVED`

There is no global generated-vs-numeric priority, overwrite, deletion, averaging, or cross-source addition. Sleep uses exact source episode/segment components only; it does not group nights.

CURRENT decisions: 4,994.

## 8. L3C feature catalog and aggregation contract

Tables:

- `derived_features`
- `derived_feature_fact_inputs`
- `derived_feature_quality_inputs`
- `derived_feature_resolution_inputs`

Definition: `l3c.features.daily` v0.1

Every feature links to all relevant L3A facts and CURRENT L3B quality/resolution evidence.

Implemented catalog:

- Heart rate, SpO2, Xiaomi stress: source/timezone-scoped daily count, mean, median, min, max.
- Resting heart rate: daily passthrough with provenance.
- Steps/calories: source-scoped observed bucket sum and bucket count; coverage is `VENDOR_BUCKET_WIDTH_UNRESOLVED`.
- Sleep: source-episode duration, vendor-stage segment count, awake duration, sleep-like duration, present-stage durations, and present-stage proportions.

CURRENT features: 229.

Daily grouping uses the preserved fact offset. When offset is absent, the preserved timezone name is used; DAILY source facts keep their existing `local_date`. Historical dates do not depend on the user's current timezone.

Missing groups produce no feature row. Missing is never automatically zero. An explicit `items=[]` Sleep record may produce stage-segment count 0 because that zero is present in source structure, while stage-duration rows remain absent.

No feature combines different `source_sid/source_class` buckets. `UNRESOLVED` decisions remain source-scoped and are attached as provenance.

No health/readiness/recovery/sleep/wellness score, baseline, anomaly, diagnosis, risk assessment, or AI reasoning is implemented.

## 9. Production schema tables

- `schema_migrations`
- `definition_registry`
- `pipeline_runs`
- `processing_checkpoints`
- `normalization_issues`
- `fact_registry`
- `fact_provenance`
- `normalized_point_facts`
- `normalized_daily_facts`
- `normalized_bucket_facts`
- `normalized_interval_facts`
- `quality_assessments`
- `quality_assessment_inputs`
- `source_resolution_decisions`
- `source_resolution_inputs`
- `derived_features`
- `derived_feature_fact_inputs`
- `derived_feature_quality_inputs`
- `derived_feature_resolution_inputs`

## 10. Migrations

1. `001_foundation.sql`
2. `002_point_fact_core.sql`
3. `003_point_fact_attributes.sql`
4. `004_daily_fact_core.sql`
5. `005_bucket_fact_core.sql`
6. `006_interval_fact_core.sql`
7. `007_l3b_quality_resolution.sql`
8. `008_l3c_derived_features.sql`

Use `scripts/apply_migrations_v0_1.py`. It verifies the complete checksum chain, applies each migration transactionally, supports an empty database, and is idempotent.

## 11. Registered definitions

- `normalize.heart_rate` v0.1
- `normalize.spo2` v0.1
- `normalize.xiaomi_stress_score` v0.1
- `normalize.resting_heart_rate` v0.1
- `normalize.steps` v0.1
- `normalize.calories` v0.1
- `normalize.sleep` v0.1
- `l3b.quality.structural` v0.1
- `l3b.resolution.source` v0.1
- `l3c.features.daily` v0.1

## 12. Canonical procedures and scripts

Full L3 rebuild:

```powershell
python D:\PersonalHealthEngine-L3\scripts\l3_full_rebuild_v0_1.py `
  --l2 D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3 `
  --production-l3 D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3 `
  --output-dir D:\PersonalHealthEngine-L3\full_rebuild_acceptance
```

This creates an L2 SQLite snapshot, builds an empty L3 through migrations 1–8, runs all L3A normalizers/checkpoint bootstraps, then L3B and L3C, and finally runs `l3_semantic_compare_v0_1.py`.

Incremental entry points:

- POINT: `l3_point_incremental_runner_v0_1.py`
- BUCKET: `l3_bucket_incremental_runner_v0_1.py`
- Sleep: `l3_sleep_incremental_runner_v0_1.py`
- L3B: `l3b_materializer_v0_1.py --mode incremental`
- L3C: `l3c_materializer_v0_1.py --mode incremental`

Full entry points:

- POINT: `l3_point_full_runner_v0_1.py`
- DAILY: `l3_daily_full_runner_v0_1.py`
- BUCKET: `l3_bucket_full_runner_v0_1.py`
- Sleep: `l3_sleep_full_runner_v0_1.py`
- L3B: `l3b_materializer_v0_1.py --mode full`
- L3C: `l3c_materializer_v0_1.py --mode full`

Acceptance/audit entry points:

- `l3a_acceptance_v0_1.py`
- `l3b_acceptance_v0_1.py`
- `l3c_acceptance_v0_1.py`
- `l3_final_audit_v0_1.py`

Legacy dataset-specific scripts remain for regression/history; canonical generic/parameterized runners above should be preferred.

## 13. Acceptance evidence

- L3A: `D:\PersonalHealthEngine-L3\L3A_ACCEPTANCE.json` — PASS 27/27
- L3B: `D:\PersonalHealthEngine-L3\L3B_ACCEPTANCE.json` — PASS 22/22
- L3C: `D:\PersonalHealthEngine-L3\L3C_ACCEPTANCE.json` — PASS 25/25
- Full rebuild: `D:\PersonalHealthEngine-L3\full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json` — PASS 18/18
- Semantic equivalence: `D:\PersonalHealthEngine-L3\full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json` — PASS 13/13 domains
- Final audit: `D:\PersonalHealthEngine-L3\L3_FINAL_AUDIT.json` — PASS 55/55

## 14. Known limitations

- Xiaomi Sleep classifications are vendor inference with no external physiological ground truth.
- Canonical Sleep night grouping is unresolved due limited nights and multi-episode behavior.
- Activity bucket width is unresolved.
- Calories physical unit/component meaning is unresolved.
- Some multi-source conflicts are deliberately `UNRESOLVED`.
- Resting-heart-rate coverage is only two days.

These limitations are part of the data contract and must not be “fixed” by guessing in Layer 4.

## 15. Layer 4 input contract

Layer 4 is Personal Baseline. Its core comparison is the current user versus the same user's own long-term normal state (`intra-person baseline`). Population references are secondary future evidence for safety boundaries, extreme values, sparse personal history, or external comparison.

Layer 4 may directly consume:

- CURRENT `derived_features` with all three provenance link families.
- CURRENT L3B quality assessments and source-resolution decisions.
- CURRENT L3A facts when a baseline model explicitly needs a lower-level input.
- Preserved timezone/local-date, source, evidence type, unit, coverage, and unresolved semantics.

Layer 4 must not redo:

- Xiaomi raw parsing or L2 identity/version resolution.
- POINT/DAILY/BUCKET/INTERVAL normalization.
- Xiaomi Sleep stage mapping.
- Steps/calories source coexistence or component resolution.
- L3 quality gating, source-resolution decisions, or daily statistical aggregation.
- Historical revision discovery/checkpoint logic already owned by L3.

Layer 4 must not reinterpret vendor output as physiological truth, fill missing values with zero without an explicit model, merge unresolved sources, or invent canonical Sleep nights. Any baseline implementation must be a new Layer 4 contract and must leave the sealed Layer 3 artifacts intact.
