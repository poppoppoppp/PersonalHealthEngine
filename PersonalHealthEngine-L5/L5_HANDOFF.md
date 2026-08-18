# Personal Health Engine — Layer 5 to Layer 6 Handoff

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
- L5 = SEALED
- L6 = NOT STARTED
- L7 = NOT STARTED

L5A/L5B/L5C/L5D/L5E are internal Layer 5 stages, not additional logical layers.

## 2. Upstream summary

- L3 (`D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3`, schema v8) provides
  CURRENT/history derived features.
- L4 (`D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3`, schema v2) provides
  source-scoped personal baseline snapshots, maturity, and quantiles.

Both are opened strictly read-only. L5 never re-queries Xiaomi Cloud, never re-parses L2,
never re-normalizes, never redoes L3 quality/source resolution, and never rebuilds L4 baselines.

## 3. Layer 5 architecture and production state

Canonical contract: `D:\PersonalHealthEngine-L5\L5_CONTRACT.md`

Production database: `D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3`

Schema version: 2

Pipeline:

`L3 CURRENT features + L4 baselines -> L5A deviation -> L5B persistence/trend -> L5C change -> L5D relationship -> L5E evidence`

Authoritative recovery is a full rebuild from L3 + L4. Incremental processing is an
optimization proven semantically equivalent to full rebuild.

## 4. What L5 produces

| Analytic | Table | Scope | Result classes |
|---|---|---|---|
| Deviation | `deviation_analytics` | per feature row × window (7/28/90) | INSUFFICIENT_BASELINE, ABOVE_TYPICAL_RANGE, BELOW_TYPICAL_RANGE, WITHIN_TYPICAL_RANGE |
| Persistence | `persistence_analytics` | per DAILY series × window | INSUFFICIENT_OBSERVATIONS, PERSISTENT_ABOVE/BELOW_TYPICAL, NO_PERSISTENT_DEVIATION |
| Trend | `trend_analytics` | per DAILY series | INSUFFICIENT_OBSERVATIONS, RISING, FALLING, STABLE |
| Change point | `change_point_analytics` | per DAILY series | INSUFFICIENT_EVIDENCE, CHANGE_DETECTED, NO_CHANGE |
| Relationship | `relationship_analytics` | per metric pair × source context | INSUFFICIENT_PAIRED_DATA, POSITIVE/NEGATIVE_ASSOCIATION, NO_ASSOCIATION |

Key semantics:

- No look-ahead: a feature on date D is compared only to the L4 baseline `as_of_date = D`.
- Robust deviation: median/MAD/quantile-based; MAD=0 yields NULL standardized deviation with
  reason `MAD_ZERO` (never division by zero).
- Baseline maturity participates: INSUFFICIENT_HISTORY baselines produce
  `INSUFFICIENT_BASELINE` (no fabricated deviation), PROVISIONAL yields weaker evidence.
- Personal deviation is NOT clinical abnormality; L5 stores no diagnosis/risk/score.
- Missing is never zero-filled or interpolated. Sources are never merged.
- Sleep series (SOURCE_EPISODE) receive deviation only; no canonical night is invented.
- Relationship is strictly statistical association (Spearman); no causal inference.

## 5. Production schema tables

- `schema_migrations`, `definition_registry`, `pipeline_runs`, `processing_checkpoints`, `analytics_issues`
- `analytics_series`
- `deviation_analytics`, `persistence_analytics`, `trend_analytics`, `change_point_analytics`, `relationship_analytics`
- `analytics_l3_inputs`, `analytics_baseline_inputs`, `upstream_input_state`

## 6. Migrations

1. `001_foundation.sql`
2. `002_analytics_core.sql`

Use `scripts/apply_migrations_v0_1.py`.

## 7. Registered definitions

- `l5a.deviation.robust` v0.1 (DEVIATION)
- `l5b.persistence` v0.1 (PERSISTENCE)
- `l5b.trend.robust` v0.1 (TREND)
- `l5c.change_point` v0.1 (CHANGE_POINT)
- `l5d.relationship` v0.1 (RELATIONSHIP)
- `l5e.evidence` v0.1 (EVIDENCE)

## 8. Canonical procedures

Full materialization:

```powershell
python D:\PersonalHealthEngine-L5\scripts\l5_analytics_materializer_v0_1.py `
  --mode full `
  --l3 D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3 `
  --l4 D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3 `
  --l5 D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3 `
  --deviation ...\l5a_deviation_robust_v0_1.json `
  --persistence ...\l5b_persistence_v0_1.json `
  --trend ...\l5b_trend_robust_v0_1.json `
  --change ...\l5c_change_point_v0_1.json `
  --relationship ...\l5d_relationship_v0_1.json `
  --evidence ...\l5e_evidence_v0_1.json
```

Incremental update: same command with `--mode incremental`.

Full rebuild + semantic equivalence:

```powershell
python D:\PersonalHealthEngine-L5\scripts\l5_full_rebuild_v0_1.py `
  --l3 ...\personal_health_features.sqlite3 `
  --l4 ...\personal_health_baselines.sqlite3 `
  --production-l5 D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3 `
  --output-dir D:\PersonalHealthEngine-L5\full_rebuild_acceptance
```

Acceptance / audit: `l5_acceptance_v0_1.py`, `l5_final_audit_v0_1.py`, `l5_semantic_compare_v0_1.py`.

## 9. Acceptance evidence

- Core acceptance: `L5_ACCEPTANCE.json` — PASS 23/23
- Full rebuild: `full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json` — PASS 6/6
- Incremental/full semantic equivalence: `full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json` — PASS 15/15
- Final audit: `L5_FINAL_AUDIT.json` — PASS 28/28
- Regression suite: `tests\test_l5_analytics_v0_1.py` — 16/16 PASS

## 10. Sealed production counts

- CURRENT analytics series: 44
- CURRENT deviation analytics: 687 (78 ABOVE, 72 BELOW, 162 WITHIN, 375 INSUFFICIENT_BASELINE)
- CURRENT persistence analytics: 72
- CURRENT trend analytics: 24 (5 FALLING, 16 STABLE, 3 INSUFFICIENT_OBSERVATIONS)
- CURRENT change-point analytics: 24 (all INSUFFICIENT_EVIDENCE)
- CURRENT relationship analytics: 4 (2 POSITIVE_ASSOCIATION, 2 NO_ASSOCIATION)
- Provenance: 1,301 L3 inputs, 1,032 baseline inputs
- Registered definitions: 6, applied migrations: 2

## 11. Known limitations

- Sleep stages/awake states are Xiaomi vendor inferences, not physiological truth.
- Canonical Sleep night grouping is unresolved; Sleep series have deviation only.
- Activity bucket widths and calories physical semantics remain unresolved.
- Resting-heart-rate coverage is sparse (2 daily facts); its timezone offset is NULL, so it
  does not share a source context with other metrics and thus has no cross-metric
  relationship in v0.1.
- Overall history is short (≈7 days), so many baselines are INSUFFICIENT_HISTORY/PROVISIONAL
  and change detection is INSUFFICIENT_EVIDENCE.

## 12. Layer 6 input contract

Layer 6 (AI Reasoning) may consume L5 analytics joined to `analytics_series` and the
`analytics_l3_inputs` / `analytics_baseline_inputs` provenance tables, using `evidence_status`
and the supporting counts to know how reliable each conclusion is.

Layer 5 intentionally does NOT provide causal attribution, diagnosis, risk, or any composite
health/readiness/recovery/sleep/wellness score or recommendation — those are Layer 6/7
responsibilities built on top of this statistical evidence layer.
