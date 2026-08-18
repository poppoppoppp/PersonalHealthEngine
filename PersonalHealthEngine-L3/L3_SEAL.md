# Layer 3 Seal

Status: SEALED

Date: 2026-08-16

## Scope and result

Layer 3 implements the canonical `L3_CONTRACT.md` pipeline:

`L2 Raw Store -> L3A Normalized Facts -> L3B Data Quality & Source Resolution -> L3C Derived Features`

- L3A: PASS (27/27)
- L3B: PASS (22/22)
- L3C: PASS (25/25)
- Final audit: PASS (55/55)
- Full rebuild: PASS (18/18)
- Incremental/full semantic equivalence: PASS

Layer states:

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: NOT STARTED
- L5 Health Analytics: NOT STARTED
- L6 AI Reasoning: NOT STARTED
- L7 Product Output: NOT STARTED

Canonical boundary declaration:

- L1 = SEALED
- L2 = SEALED
- L3 = SEALED
- L4 = NOT STARTED

## Production contract

- Contract: `D:\PersonalHealthEngine-L3\L3_CONTRACT.md`
- Production schema version: 8
- L2 source: `D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3` (read-only)
- L3 database: `D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3`
- Final audit: `D:\PersonalHealthEngine-L3\L3_FINAL_AUDIT.json`
- Rebuild report: `D:\PersonalHealthEngine-L3\full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json`
- Semantic comparison: `D:\PersonalHealthEngine-L3\full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json`

## Sealed production counts

- CURRENT normalized facts: 5,179
  - heart_rate: 2,614
  - spo2: 385
  - xiaomi_stress_score: 483
  - resting_heart_rate: 2
  - steps: 468
  - calories: 1,064
  - sleep_source_episode: 14
  - sleep_vendor_stage_segment: 149
- CURRENT quality assessments: 15,537
- CURRENT source-resolution decisions: 4,994
- CURRENT derived features: 229
- Registered definitions: 10
- Applied migrations: 8
- Processing checkpoints: 9

## Known limitations

- Xiaomi Sleep stages and sleep/awake states are vendor inferences, not physiological truth.
- Canonical Sleep night grouping remains unresolved; the available sample is insufficient for a stable gap threshold.
- Activity bucket widths remain `VENDOR_UNRESOLVED`.
- Calories retain `vendor_calories`; the physical unit and component meaning are unresolved.
- Some multi-source conflicts remain explicitly `UNRESOLVED`.
- Resting-heart-rate coverage is sparse (2 daily facts).
- No external physiological ground truth is available.

These are explicit evidence limitations, not hidden correctness defects. Layer 3 does not contain personal baselines, anomaly analysis, medical diagnosis, health-risk scoring, or AI reasoning.
