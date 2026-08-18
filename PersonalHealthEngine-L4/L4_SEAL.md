# Layer 4 Seal

Status: SEALED

Date: 2026-08-16

## Scope and result

Layer 4 implements the canonical `L4_CONTRACT.md` personal baseline pipeline:

`L3 CURRENT derived_features -> L4A Eligibility -> L4B Series -> L4C Rolling Baseline -> L4D Maturity`

- Core acceptance: PASS (25/25)
- Full rebuild: PASS (5/5)
- Incremental/full semantic equivalence: PASS (11/11)
- Final audit: PASS (29/29)
- Regression suite: PASS (11/11)

Layer states:

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: SEALED
- L5 Health Analytics: NOT STARTED
- L6 AI Reasoning: NOT STARTED
- L7 Product Output: NOT STARTED

Canonical boundary declaration:

- L1 = SEALED
- L2 = SEALED
- L3 = SEALED
- L4 = SEALED

## Production contract

- Contract: `D:\PersonalHealthEngine-L4\L4_CONTRACT.md`
- Production schema version: 2
- Upstream L3: `D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3` (read-only, schema version 8)
- L4 database: `D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3`
- Final audit: `D:\PersonalHealthEngine-L4\L4_FINAL_AUDIT.json`
- Acceptance: `D:\PersonalHealthEngine-L4\L4_ACCEPTANCE.json`
- Rebuild report: `D:\PersonalHealthEngine-L4\full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json`
- Semantic comparison: `D:\PersonalHealthEngine-L4\full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json`

## Sealed production counts

- CURRENT baseline series: 44
- CURRENT rolling baselines: 1,056 (352 per window × 3 windows)
- Maturity distribution: ESTABLISHED 74, PROVISIONAL 412, INSUFFICIENT_HISTORY 570
- Baseline provenance inputs: 2,559
- Registered definitions: 4
- Applied migrations: 2
- Processing checkpoints: 1

## Core guarantees

- No look-ahead leakage: every baseline as of date D uses only observations with
  `local_date < D` (verified by acceptance L4-09/L4-10 and the regression suite).
- Source isolation: `NUMERIC_SOURCE` and `XIAOMI_GENERATED` are never merged.
- Missing is never zero-filled or interpolated.
- Sleep baselines invent no canonical night; Xiaomi stages remain vendor inference.
- Steps/calories retain `VENDOR_BUCKET_WIDTH_UNRESOLVED` and are not reinterpreted.
- `INSUFFICIENT_HISTORY` baselines publish no statistics (no fabricated "normal level").
- No anomaly/trend/risk/score/recommendation output exists in Layer 4.

## Known limitations

- Sleep stages and sleep/awake states are Xiaomi vendor inferences, not physiological truth.
- Canonical Sleep night grouping remains unresolved.
- Activity bucket widths remain `VENDOR_UNRESOLVED`.
- Calories retain `vendor_calories`; the physical unit and component meaning are unresolved.
- Resting-heart-rate coverage is sparse (2 daily facts); many baselines are
  `INSUFFICIENT_HISTORY` or `PROVISIONAL` by design.
- No external physiological ground truth is available.

These are explicit evidence limitations, not hidden correctness defects. Layer 4 does not
contain anomaly analysis, trend detection, medical diagnosis, health-risk scoring, or AI
reasoning — those belong to Layer 5 and Layer 6.
