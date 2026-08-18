# Layer 5 Seal

Status: SEALED

Date: 2026-08-16

## Scope and result

Layer 5 implements the canonical `L5_CONTRACT.md` health-analytics pipeline:

`L3 CURRENT features + L4 baselines -> L5A Deviation -> L5B Persistence/Trend -> L5C Change Detection -> L5D Relationship -> L5E Evidence`

- Core acceptance: PASS (23/23)
- Full rebuild: PASS (6/6)
- Incremental/full semantic equivalence: PASS (15/15)
- Final audit: PASS (28/28)
- Regression suite: PASS (16/16)

Layer states:

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: SEALED
- L5 Health Analytics: SEALED
- L6 AI Reasoning: NOT STARTED
- L7 Product Output: NOT STARTED

Canonical boundary declaration:

- L1 = SEALED
- L2 = SEALED
- L3 = SEALED
- L4 = SEALED
- L5 = SEALED

## Production contract

- Contract: `D:\PersonalHealthEngine-L5\L5_CONTRACT.md`
- Production schema version: 2
- Upstream L3: `D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3` (read-only, schema v8)
- Upstream L4: `D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3` (read-only, schema v2)
- L5 database: `D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3`
- Final audit: `D:\PersonalHealthEngine-L5\L5_FINAL_AUDIT.json`
- Acceptance: `D:\PersonalHealthEngine-L5\L5_ACCEPTANCE.json`
- Rebuild report: `D:\PersonalHealthEngine-L5\full_rebuild_acceptance\FULL_REBUILD_ACCEPTANCE.json`
- Semantic comparison: `D:\PersonalHealthEngine-L5\full_rebuild_acceptance\SEMANTIC_EQUIVALENCE.json`

## Sealed production counts

- CURRENT analytics series: 44
- CURRENT deviation analytics: 687 (ABOVE_TYPICAL_RANGE 78, BELOW_TYPICAL_RANGE 72, WITHIN_TYPICAL_RANGE 162, INSUFFICIENT_BASELINE 375)
- CURRENT persistence analytics: 72
- CURRENT trend analytics: 24 (FALLING 5, STABLE 16, INSUFFICIENT_OBSERVATIONS 3)
- CURRENT change-point analytics: 24 (all INSUFFICIENT_EVIDENCE)
- CURRENT relationship analytics: 4 (POSITIVE_ASSOCIATION 2, NO_ASSOCIATION 2)
- Provenance inputs: 1,301 L3 feature inputs, 1,032 L4 baseline inputs
- Registered definitions: 6
- Applied migrations: 2

## Core guarantees

- No look-ahead: every feature on date D is compared only to the L4 baseline `as_of_date = D`.
- Robust deviation (median/MAD/quantiles); MAD=0 yields NULL standardized deviation with
  reason `MAD_ZERO`, never division by zero.
- Baseline maturity participates: INSUFFICIENT_HISTORY baselines produce
  `INSUFFICIENT_BASELINE`, never a fabricated deviation.
- Missing is never zero-filled or interpolated; sources are never merged.
- Sleep series receive deviation only; no canonical night is invented.
- Relationship is statistical association only (Spearman); no causal inference.
- No diagnosis, risk, health/readiness/recovery/sleep/wellness score, or recommendation exists.

## Known limitations

- Sleep stages and sleep/awake states are Xiaomi vendor inferences, not physiological truth.
- Canonical Sleep night grouping remains unresolved.
- Activity bucket widths remain `VENDOR_UNRESOLVED`; calories physical semantics unresolved.
- Resting-heart-rate coverage is sparse (2 daily facts); its NULL timezone offset prevents a
  shared source context for cross-metric relationships in v0.1.
- Overall history is short (≈7 days), so many baselines are INSUFFICIENT_HISTORY/PROVISIONAL
  and change detection is INSUFFICIENT_EVIDENCE.

These are explicit evidence limitations, not hidden correctness defects. Layer 5 does not
contain causal reasoning, diagnosis, health-risk scoring, or AI recommendations — those belong
to Layer 6 and Layer 7.
