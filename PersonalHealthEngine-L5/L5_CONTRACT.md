# Personal Health Engine
# Layer 5 Architecture & Health Analytics Contract v0.1

Status: ACTIVE (implementation in progress)
Date: 2026-08-16
Layer: Layer 5 = Health Analytics

---

## 1. Layer Boundary

Layer 5 answers: *"relative to this user's own history, what is happening now?"*

It consumes CURRENT/history L3 derived features plus L4 personal baselines and produces
deterministic, explainable, auditable statistical analytics.

Layer 5 does NOT perform:

- disease diagnosis or disease probability
- medical risk conclusions
- causal attribution ("because X, therefore Y")
- composite health / readiness / recovery / sleep / wellness scores
- AI-generated recommendations, action or treatment advice

Those belong to Layer 6 (AI Reasoning) and are explicitly out of scope.

Conceptual division:

- L3 = what today's features are
- L4 = what this user usually looks like (the ruler)
- L5 = what changed relative to that ruler (statistical, personal, non-causal)
- L6 = why it may be happening and what it means

---

## 2. Storage Architecture

Canonical upstreams (read-only):

- D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3 (schema v8)
- D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3 (schema v2)

Canonical L5 root:

D:\PersonalHealthEngine-L5

Canonical L5 database:

D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3

Rules:

- L3 and L4 are opened strictly read-only.
- L5 uses its own independent SQLite database.
- L5 is derived, materialized, and rebuildable state.
- Deleting L5 while preserving L3/L4 and L5 code/definitions must allow a full rebuild.
- L5 never re-queries Xiaomi Cloud, never re-parses L2, never re-normalizes, never redoes
  L3 quality/source resolution, and never rebuilds L4 baselines.

---

## 3. Internal Layer 5 Structure

L3 CURRENT/history features + L4 baselines
↓
L5A Deviation Analytics (current vs personal baseline)
↓
L5B Persistence & Trend
↓
L5C Change Detection (structural change)
↓
L5D Cross-Metric Relationship (statistical association only)
↓
L5E Evidence Strength

These are internal engineering stages, not new logical layers.

---

## 4. Upstream Input Contract

L5 reads:

- L3 `derived_features` (CURRENT), including `local_date`, `value_num`, `unit`,
  `provider`, `source_sid`, `source_class`, `timezone_name`, `timezone_offset_seconds`,
  `coverage_status`, `scope_type`, `feature_name`.
- L4 `baseline_series` (CURRENT) and `rolling_baselines` (CURRENT), including `median`,
  `mad`, `q10/q25/q50/q75/q90`, `maturity`, `as_of_date`, `window_days`, `observation_count`,
  `calendar_coverage`.

L5 does not redo any L2/L3/L4 work.

---

## 5. Series Identity

L5 inherits L4's source-scoped series identity verbatim:
(feature_name, scope_type, provider, source_sid, source_class, timezone_name,
timezone_offset_seconds, unit). L5 mirrors L4 CURRENT `baseline_series` into its own
`analytics_series` table so analytics are self-contained and reference L4 series by
`l4_series_id` + `series_key`.

---

## 6. No Look-Ahead Rule (inherited and enforced)

A feature on date D is compared only against the L4 baseline with `as_of_date = D`, which
by L4 contract uses only observations with `local_date < D`. L5 never uses `as_of_date > D`
(or any future baseline) to analyze date D. This is enforced by construction and by tests.

---

## 7. Time Semantics, Missing, and Source Isolation

- L3/L4 local-date and timezone semantics are inherited; L5 never reinterprets dates with
  the machine timezone.
- `missing != zero`: no forward fill, backward fill, or interpolation. Calendar gaps are
  gaps, never implicit zeros.
- Sources are never merged. Each series is source-scoped, and cross-metric relationships
  require an explicit shared source context.

---

## 8. L5A — Deviation Analytics

For every CURRENT L3 feature (numeric `value_num`) and each window (7/28/90), compare the
feature's value to the L4 baseline `as_of_date = local_date` for the same series.

Outputs:

- baseline median, baseline MAD, baseline maturity
- current value
- absolute deviation = current - median
- relative deviation = (current - median) / median (only when mathematically and
  semantically applicable; see unit policy)
- robust standardized deviation = (current - median) / MAD
- quantile position (ordinal band from L4 quantiles)
- deviation side (ABOVE / BELOW / WITHIN relative to median)
- deviation class (headline)
- evidence status

deviation class:

- `INSUFFICIENT_BASELINE` — baseline maturity is `INSUFFICIENT_HISTORY` or statistics NULL
- `ABOVE_TYPICAL_RANGE` — current > Q90
- `BELOW_TYPICAL_RANGE` — current < Q10
- `WITHIN_TYPICAL_RANGE` — Q10 <= current <= Q90

MAD safety: if MAD is 0, robust standardized deviation is NULL with reason `MAD_ZERO`
(never division by zero, never an "infinite anomaly"). If baseline statistics are NULL, no
deviation is computed.

Relative-deviation unit policy (versioned): applicable for units
{bpm, percent, seconds, steps, vendor_calories}; not applicable for {count, ratio,
vendor_score}.

Personal deviation is NOT clinical abnormality. The schema and contract keep these separate.

---

## 9. L5B — Persistence & Trend

Applies to DAILY series only. `SOURCE_EPISODE` (Sleep) series have no canonical night, so
persistence/trend report `NOT_APPLICABLE`.

### Persistence

Per (series, window), at the series' latest observation date, compute the trailing number of
consecutive observations (ending at the latest) whose deviation class is `ABOVE_TYPICAL_RANGE`
(`consecutive_above_typical`) and `BELOW_TYPICAL_RANGE` (`consecutive_below_typical`).

- `PERSISTENT_ABOVE_TYPICAL` — consecutive_above_typical >= min_consecutive
- `PERSISTENT_BELOW_TYPICAL` — consecutive_below_typical >= min_consecutive
- `NO_PERSISTENT_DEVIATION` — otherwise
- `INSUFFICIENT_OBSERVATIONS` — fewer than min_consecutive total observations or baselines
  insufficient for the trailing observations

Consecutive observations are consecutive in the observation sequence, which is NOT
automatically consecutive calendar days; calendar coverage is reported separately.

### Trend

Per (series), over the trailing observations (up to `max_trend_points`, ending at latest):

- Theil-Sen slope (median of pairwise value/day slopes) — robust slope
- Spearman rank correlation between observation index and value — monotonicity
- classification: `INSUFFICIENT_OBSERVATIONS` (< min_trend_points), `RISING`, `FALLING`,
  `STABLE`

Flat (zero-spread) series are `STABLE`.

---

## 10. L5C — Change Detection

Per (series), a conservative robust median-shift detector over the trailing observations.

- `INSUFFICIENT_EVIDENCE` — fewer than `min_change_points` observations (the correct answer
  for short history; not "no change")
- Otherwise search candidate split points and report `CHANGE_DETECTED` (with candidate date
  and shift magnitude) or `NO_CHANGE`

v0.1 does NOT classify ordinary day-to-day fluctuation as structural change.

---

## 11. L5D — Cross-Metric Relationship

Per configured metric pair and shared source context, align two DAILY series by local_date
and compute Spearman rank correlation over the paired observations.

- `INSUFFICIENT_PAIRED_DATA` — fewer than `min_paired` paired observations
- Otherwise `POSITIVE_ASSOCIATION` / `NEGATIVE_ASSOCIATION` / `NO_ASSOCIATION` by a versioned
  rho threshold

Strictly statistical association. No causal inference. Missing dates are excluded (never
zero-filled). Date alignment, source compatibility, and minimum pairs are explicit.

v0.1 pair catalog (source-scoped; each instance requires a shared source context):

1. heart_rate.daily.mean ↔ spo2.daily.mean
2. xiaomi_stress_score.daily.mean ↔ heart_rate.daily.mean
3. xiaomi_stress_score.daily.mean ↔ resting_heart_rate.daily.value
4. steps.daily.sum ↔ resting_heart_rate.daily.value
5. calories.daily.sum ↔ steps.daily.sum

---

## 12. L5E — Evidence Strength

Every analytic carries an explicit `evidence_status`:

- `SUFFICIENT` — baseline ESTABLISHED (or, for relationships, enough pairs)
- `PROVISIONAL` — baseline PROVISIONAL
- `INSUFFICIENT_BASELINE`
- `INSUFFICIENT_OBSERVATIONS`
- `INSUFFICIENT_PAIRED_DATA`
- `INSUFFICIENT_EVIDENCE`

Plus transparent supporting fields (baseline maturity, observation counts, coverage) — never
an opaque 0-100 score.

---

## 13. Provenance

Every analytic traces, via input relation tables, to:

- L3 feature inputs (`analytics_l3_inputs`)
- L4 baseline inputs (`analytics_baseline_inputs`)

which in turn reach L3/L4 quality/source context and ultimately L2. Provenance is relational,
not copied data.

---

## 14. Versioned Definitions

All algorithms are versioned in `definitions/` and registered in `definition_registry` with
file SHA-256. A definition version whose bytes change must fail on checksum mismatch.

- `l5a.deviation.robust` v0.1
- `l5b.persistence` v0.1
- `l5b.trend.robust` v0.1
- `l5c.change_point` v0.1
- `l5d.relationship` v0.1
- `l5e.evidence` v0.1

---

## 15. Revision, Incremental, and Full Rebuild

- A new L3 feature on date D affects deviation for date D and persistence/trend/change for
  the affected series; the affected analytics are recomputed.
- An L3 historical revision affects analytics whose inputs include that feature.
- An L4 baseline revision affects analytics that depend on that baseline.

Incremental processing detects the L3/L4 input-state delta and materializes only changed
rows. Full rebuild recomputes everything from scratch. Both must produce semantically
identical CURRENT state, compared by analytic identity, type, target feature/date, baseline
identity, window, deviation/persistence/trend/change/relationship results, evidence state,
provenance input sets, and lifecycle state (not row counts).

---

## 16. Core Acceptance Criteria v0.1

01 SQLite integrity
02 Foreign keys
03 L5/L3/L4 isolation (upstreams read-only)
04 Production schema version
05 Migration chain + checksums
06 Definition registry integrity
07 Deviation correctness (at center, above, below)
08 MAD=0 safe fallback
09 Baseline maturity participates (INSUFFICIENT_BASELINE)
10 Persistence classification
11 Trend classification (rising/falling/stable)
12 Change detection conservative (INSUFFICIENT_EVIDENCE on short history)
13 Relationship: association only, no causality
14 Source isolation
15 Missing != zero
16 No look-ahead leakage
17 Sleep: no canonical night; persistence/trend NOT_APPLICABLE
18 Steps/calories coverage preserved
19 Provenance complete
20 Full rebuild
21 Incremental/full semantic equivalence
22 Revision recomputes affected analytics
23 No L6 causal/score/recommendation leakage
24 Regression suite passes

---

## 17. Known Data Limitations (inherited)

- Sleep stages are Xiaomi vendor inference, not physiological truth.
- Canonical sleep night grouping is unresolved.
- Activity bucket widths and calories physical semantics are unresolved.
- Resting-heart-rate coverage is sparse (2 daily facts).
- Overall history is short (≈7 days), so most baselines are INSUFFICIENT_HISTORY/PROVISIONAL
  and change detection is INSUFFICIENT_EVIDENCE.

These are part of the data contract and must not be "fixed" by guessing.

---

## 18. Current Layer Status

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: SEALED
- L5 Health Analytics: IN DEVELOPMENT
- L6 AI Reasoning: NOT STARTED
- L7 Product Output: NOT STARTED
