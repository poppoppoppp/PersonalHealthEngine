# Personal Health Engine — Layer 4 to Layer 5 Handoff

## 1. Project and fixed architecture

Personal Health Engine converts continuous health and behavior data into versioned,
provenance-aware evidence. The fixed logical architecture is:

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
- L5 = NOT STARTED
- L6 = NOT STARTED
- L7 = NOT STARTED

L4A/L4B/L4C/L4D are internal Layer 4 stages, not additional logical layers.

## 2. Upstream summary

Layer 3 is the sealed, immutable feature source at
`D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3` (schema version 8).
Layer 4 opens it strictly read-only and consumes only CURRENT `derived_features`. It never
re-queries Xiaomi Cloud, never re-parses L2 raw data, and never redoes L3A/L3B/L3C work.

## 3. Layer 4 architecture and production state

Canonical contract: `D:\PersonalHealthEngine-L4\L4_CONTRACT.md`

Production database: `D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3`

Schema version: 2

Pipeline:

`L3 CURRENT derived_features -> L4A eligibility -> L4B series -> L4C rolling baseline -> L4D maturity`

Authoritative recovery is a full rebuild from L3. Incremental processing is an optimization
and is proven semantically equivalent to full rebuild.

## 4. What L4 produces

For every eligible source-scoped L3 derived feature, L4 produces a rolling personal baseline
for windows 7 / 28 / 90 days, parameterized by an as-of date with a strict no-look-ahead rule:

- baseline **as of date D** with window **W** uses observations with `local_date in [D - W, D - 1]`
- D and later dates are excluded

Each baseline records: observation_count, distinct_observation_dates, history_span_days,
calendar_coverage, mean, median, MAD, Q10/Q25/Q50/Q75/Q90, and a maturity of
`INSUFFICIENT_HISTORY | PROVISIONAL | ESTABLISHED`. `INSUFFICIENT_HISTORY` baselines publish
no statistics (they are NULL), so a small sample is never presented as a "normal level".

## 5. Semantics inherited from L3 (never reinterpreted)

- Sources `NUMERIC_SOURCE` and `XIAOMI_GENERATED` are always separate series.
- Missing days are never zero-filled, forward/backward filled, or interpolated.
- Sleep baselines use only `sleep_source_episode.*` derived features; no canonical night is
  invented and Xiaomi stages remain `VENDOR_INFERENCE`.
- Steps/calories remain source-scoped with `VENDOR_BUCKET_WIDTH_UNRESOLVED` preserved; they
  are not reinterpreted as total daily energy expenditure or complete full-day steps.

## 6. Production schema tables

- `schema_migrations`
- `definition_registry`
- `pipeline_runs`
- `processing_checkpoints`
- `baseline_issues`
- `baseline_series`
- `rolling_baselines`
- `baseline_feature_inputs`
- `baseline_input_state`

## 7. Migrations

1. `001_foundation.sql`
2. `002_baseline_core.sql`

Use `scripts/apply_migrations_v0_1.py` (verifies the checksum chain, transactional, idempotent).

## 8. Registered definitions

- `l4a.baseline.eligibility` v0.1 (ELIGIBILITY)
- `l4b.baseline.series` v0.1 (SERIES)
- `l4c.baseline.windows` v0.1 (WINDOW)
- `l4d.baseline.maturity` v0.1 (MATURITY)

## 9. Canonical procedures and scripts

Full materialization:

```powershell
python D:\PersonalHealthEngine-L4\scripts\l4_baseline_materializer_v0_1.py `
  --mode full `
  --l3 D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3 `
  --l4 D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3 `
  --eligibility D:\PersonalHealthEngine-L4\definitions\eligibility\l4a_baseline_eligibility_v0_1.json `
  --series D:\PersonalHealthEngine-L4\definitions\series\l4b_baseline_series_v0_1.json `
  --windows D:\PersonalHealthEngine-L4\definitions\windows\l4c_baseline_windows_v0_1.json `
  --maturity D:\PersonalHealthEngine-L4\definitions\maturity\l4d_baseline_maturity_v0_1.json
```

Incremental update: same command with `--mode incremental`.

Full rebuild + semantic equivalence:

```powershell
python D:\PersonalHealthEngine-L4\scripts\l4_full_rebuild_v0_1.py `
  --l3 D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3 `
  --production-l4 D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3 `
  --output-dir D:\PersonalHealthEngine-L4\full_rebuild_acceptance
```

Acceptance / audit:

- `l4_acceptance_v0_1.py`
- `l4_final_audit_v0_1.py`
- `l4_semantic_compare_v0_1.py`

## 10. Acceptance evidence

- Core acceptance: `L4_ACCEPTANCE.json` — PASS 25/25
- Full rebuild: `full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json` — PASS 5/5
- Semantic equivalence: `full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json` — PASS 11/11
- Final audit: `L4_FINAL_AUDIT.json` — PASS 29/29
- Regression suite: `tests\test_l4_baseline_v0_1.py` — 11/11 PASS

## 11. Sealed production counts

- CURRENT baseline series: 44
- CURRENT rolling baselines: 1,056 (352 per window × 3 windows)
- Maturity distribution: ESTABLISHED 74, PROVISIONAL 412, INSUFFICIENT_HISTORY 570
- Baseline provenance inputs: 2,559
- Registered definitions: 4
- Applied migrations: 2

## 12. Known limitations

- Sleep stages/awake states are Xiaomi vendor inferences, not physiological truth.
- Canonical Sleep night grouping remains unresolved.
- Activity bucket widths remain `VENDOR_UNRESOLVED`.
- Calories retain `vendor_calories`; physical unit/component meaning unresolved.
- Resting-heart-rate coverage is sparse (2 daily facts); many baselines are
  `INSUFFICIENT_HISTORY` or `PROVISIONAL` by design.
- No external physiological ground truth is available.

## 13. Layer 5 input contract

Layer 5 (Health Analytics) may consume CURRENT `rolling_baselines` joined to
`baseline_series`, using:

- the as-of date and window to select the correct reference frame,
- the robust statistics (median/MAD/quantiles) and `maturity` to decide what is reliable,
- `observation_count`, `calendar_coverage`, and `history_span_days` to weight confidence,
- `baseline_feature_inputs` to trace every baseline back to L3 features.

Layer 4 intentionally does NOT provide anomaly scores, trend/change-point decisions, risk
judgments, or any health/readiness/recovery/sleep/wellness score — those are Layer 5/6
responsibilities built on top of this "ruler."
