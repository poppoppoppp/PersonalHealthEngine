# Personal Health Engine
# Layer 4 Architecture & Personal Baseline Contract v0.1

Status: ACTIVE (implementation in progress)
Date: 2026-08-16
Layer: Layer 4 = Personal Baseline

---

## 1. Layer Boundary

Layer 4 converts Layer 3's long-term feature time series into a **personal baseline**: a
versioned, as-of-aware, source-scoped statistical reference describing *"what this user
themselves usually looks like."*

Layer 4 does NOT perform:

- anomaly / deviation judgment
- trend / change-point detection
- disease / risk judgment
- health / readiness / recovery / sleep / wellness scoring
- causal reasoning or recommendations

Those belong to Layer 5 and Layer 6.

Conceptual division:

- L3 = metrics (what the data/features are, per day or per episode)
- L4 = personal historical reference frame (the "ruler")
- L5 = current state relative to history (what happened relative to the ruler)

---

## 2. Storage Architecture

Canonical upstream (read-only):

D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3

Canonical L4 root:

D:\PersonalHealthEngine-L4

Canonical L4 database:

D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3

Rules:

- L3 is opened strictly read-only by L4.
- L4 uses an independent SQLite database.
- L4 is derived, materialized, and rebuildable state.
- Deleting L4 while preserving L3 and L4 code/definitions must allow a full rebuild.
- L4 never re-queries Xiaomi Cloud and never re-parses L2 raw data.

---

## 3. Internal Layer 4 Structure

L3 CURRENT derived_features
↓
L4A Baseline Eligibility
↓
L4B Baseline Series
↓
L4C Rolling Personal Baseline
↓
L4D Baseline Maturity

These are internal engineering stages only, not new logical layers. The official seven-layer
architecture remains unchanged.

---

## 4. Upstream Input Contract

L4 consumes **CURRENT `derived_features`** from the sealed L3 database, including their
preserved `feature_name`, `scope_type`, `scope_key`, `local_date`, `value_num`, `unit`,
`provider`, `source_sid`, `source_class`, `timezone_name`, `timezone_offset_seconds`, and
`coverage_status`.

L4 does NOT redo:

- Xiaomi raw parsing or L2 identity/version resolution
- POINT/DAILY/BUCKET/INTERVAL normalization
- Xiaomi Sleep stage mapping
- Steps/calories source coexistence or component resolution
- L3 quality gating, source-resolution decisions, or daily statistical aggregation
- L3 revision discovery/checkpoint logic

L3 feature provenance, L3 quality evidence, and L3 source-resolution evidence remain
traceable through the L3 `derived_features.*_inputs` link families; L4 records the L3
`derived_features.id` values it consumed so the full L3 chain stays reachable.

---

## 5. L4A — Baseline Eligibility

Eligible inputs:

- `derived_features.status = 'CURRENT'`
- `value_num IS NOT NULL` (numeric only)

All eligible numeric derived features are baselined. Rules that must hold:

- Source isolation: `NUMERIC_SOURCE` and `XIAOMI_GENERATED` are never merged to increase
  sample size. Each forms its own series.
- `missing != zero`: missing days produce no observation. No zero-fill, no forward/backward
  fill, no interpolation.
- Sleep: only the existing `sleep_source_episode.*` derived features are baselined. No
  canonical sleep night is invented. Xiaomi vendor stages are not treated as physiological
  truth.
- Steps/calories: baselined source-scoped with `VENDOR_BUCKET_WIDTH_UNRESOLVED` preserved.
  They are not reinterpreted as total daily energy expenditure or complete full-day steps.

---

## 6. L4B — Baseline Series

A series is one personal historical time series. Its identity is the canonical JSON of:

- feature_name
- scope_type (DAILY | SOURCE_EPISODE)
- provider
- source_sid
- source_class
- timezone_name
- timezone_offset_seconds
- unit

Observation time = `local_date`. Observation value = `value_num`.

For `SOURCE_EPISODE` series, episode-specific `scope_key` members
(`episode_start_utc`, `episode_end_utc`, `l2_logical_record_id`) are excluded from series
identity; the observation time is the episode `local_date` (wake date). Multiple episodes may
share one local_date and each contributes an observation.

A series with a changed source, unit, or timezone is a different series.

---

## 7. L4C — Rolling Personal Baseline

Supported windows: 7, 28, 90 days. The window set is versioned and not hardcoded to one value.

### As-of rule (no look-ahead leakage)

A baseline **as of date D** with window **W** uses exactly the observations whose
`local_date` is in `[D - W, D - 1]`. Date D and any later date are excluded. This means the
baseline used to assess "today" (D) is built only from yesterday and earlier.

As-of range: from `min(local_date)` to `max(local_date) + 1` inclusive, so the latest
materialized baseline is the one used to assess the day after the newest data.

### Statistics

Every baseline records:

- observation_count
- distinct_observation_dates
- history_span_days (calendar days from first to last observed date, inclusive)
- calendar_coverage (distinct_observation_dates / window_days)
- mean
- median
- MAD (median absolute deviation around the median)
- Q10, Q25, Q50, Q75, Q90

Statistics use robust methods first (median, MAD, quantiles). Health data is not assumed
normal, and mean ± standard deviation is not the primary model. Quantiles use linear
interpolation over sorted values (type 7); `median == Q50` by construction.

---

## 8. L4D — Baseline Maturity

Maturity is an **engineering data-sufficiency** measure, not a medical judgment.

- `INSUFFICIENT_HISTORY` — observation_count below `min_observations`. Statistics are NOT
  published as a "normal level" (statistical fields are NULL).
- `PROVISIONAL` — enough observations to compute statistics, but below the established bar.
- `ESTABLISHED` — meets the per-window established thresholds.

Per-window thresholds are versioned in `l4d.baseline.maturity`. v0.1:

| window | min_observations | established_min_observations | established_min_span_days | established_min_coverage |
|---|---:|---:|---:|---:|
| 7  | 3 | 5  | 5  | 0.60 |
| 28 | 3 | 14 | 14 | 0.50 |
| 90 | 3 | 30 | 30 | 0.33 |

Example: two days of resting heart rate produce `INSUFFICIENT_HISTORY`, never a fabricated
"normal average."

---

## 9. Provenance

Every rolling baseline links (N:M) to the L3 `derived_features.id` values it consumed, with a
denormalized snapshot of `l3_feature_name` and `l3_local_date` for self-contained
traceability. The L3 link families (fact / quality / resolution) remain available in L3.

---

## 10. Revision, Incremental, and Full Rebuild

A change to an L3 feature on local_date X affects every baseline whose window contains X,
i.e. baselines as of dates `[X + 1, X + W]` for window W.

- Full rebuild: compute the complete baseline set and materialize the delta (stale changed
  rows, insert new rows). Idempotent.
- Incremental: detect the L3 input-state delta, recompute only the affected as-of region,
  and materialize the delta. Idempotent.

Full rebuild and incremental must produce semantically identical CURRENT state. Equivalence is
verified by comparing series identity, as-of date, window, statistics, maturity, and
provenance inputs (not row counts).

---

## 11. Core Acceptance Criteria v0.1

Layer 4 Core Acceptance must at minimum validate:

01 SQLite integrity
02 Foreign keys
03 L4 / L3 database isolation (L3 read-only)
04 Production schema version
05 Migration chain + checksums
06 Definition registry integrity
07 Windows 7/28/90 present
08 As-of no-look-ahead leakage
09 Robust statistics correctness (median/MAD/quantiles)
10 Maturity thresholds and INSUFFICIENT_HISTORY behavior
11 Source isolation (no cross-source merge)
12 Missing != zero (no fabrication)
13 Sleep: no canonical night invented
14 Steps/calories: coverage preserved, no reinterpretation
15 Full provenance (N:M to L3 features)
16 Full rebuild
17 Incremental/full semantic equivalence
18 Revision recomputes affected baselines
19 No L5 anomaly/score or L6 reasoning leakage
20 Regression suite passes

---

## 12. Known Data Limitations (inherited from L3)

- Sleep stages/awake states are Xiaomi vendor inferences, not physiological truth.
- Canonical sleep night grouping remains unresolved.
- Activity bucket widths remain `VENDOR_UNRESOLVED`.
- Calories retain `vendor_calories`; physical unit/component meaning unresolved.
- Resting-heart-rate coverage is sparse (2 daily facts).
- Some multi-source conflicts remain explicitly UNRESOLVED.

These limitations are part of the data contract; L4 must not "fix" them by guessing.

---

## 13. Current Layer Status

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: IN DEVELOPMENT
- L5 Health Analytics: NOT STARTED
- L6 AI Reasoning: NOT STARTED
- L7 Product Output: NOT STARTED
