# Personal Health Engine
# Layer 3 Architecture & Feature Contract v0.1

Status: SEALED
Date: 2026-08-16
Layer: Layer 3 = Feature Engineering

---

## 1. Layer Boundary

Layer 3 converts vendor-specific, versioned raw health facts from Layer 2 into:

- normalized health facts
- quality / coverage evidence
- source-resolution results
- deterministic derived features

Layer 3 does NOT perform:

- personal baseline modeling
- anomaly interpretation
- medical judgment
- causal health reasoning
- AI recommendations

These belong to later layers.

Layer 2 is the immutable raw source of truth.

Normal runtime:

L1 Collector
-> L2 Raw Store
-> L3 Feature Pipeline

L3 must never re-query Xiaomi Cloud during normal processing.

---

## 2. Storage Architecture

Canonical L2 database:

D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3

Canonical L3 root:

D:\PersonalHealthEngine-L3

Canonical L3 database:

D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3

Rules:

- L2 is read-only from L3.
- L3 uses an independent SQLite database.
- L3A / L3B / L3C share one L3 database.
- L3 is derived, materialized, and rebuildable state.
- Deleting L3 while preserving L2 and L3 code/definitions must allow a full rebuild.
- v0.1 does not require PostgreSQL, DuckDB, Parquet, or other core storage systems.

---

## 3. Internal Layer 3 Structure

L2 Raw Store
↓
L3A Normalized Facts
↓
L3B Data Quality & Source Resolution
↓
L3C Derived Features

These are internal engineering stages only.

The official seven-layer architecture remains unchanged.

---

## 4. Standard Fact Dimensions

Every normalized health fact must distinguish:

### Temporal Type

- POINT
- DAILY
- BUCKET
- INTERVAL

### Evidence Type

- SENSOR_DERIVED
- VENDOR_DERIVED
- VENDOR_INFERRED

Temporal structure and evidence semantics are independent.

Examples:

heart_rate:
- temporal_type = POINT
- evidence_type = SENSOR_DERIVED

xiaomi_stress_score:
- temporal_type = POINT
- evidence_type = VENDOR_DERIVED

xiaomi sleep stage:
- temporal_type = INTERVAL
- evidence_type = VENDOR_INFERRED

---

## 5. Standard Fact Contract

A normalized fact must be able to represent:

### Identity

- metric
- temporal_type
- evidence_type

### Time

Depending on temporal_type:

POINT:
- event_time_utc

DAILY:
- local_date

BUCKET:
- bucket_anchor_time_utc

INTERVAL:
- start_time_utc
- end_time_utc

### Value

As applicable:

- value_num
- value_code
- unit

Not every fact is required to contain a numeric value.

### Source

- provider
- source_sid
- source_class
- timezone_name
- timezone_offset

### Provenance

Every L3 fact must be traceable to one or more:

- L2 logical_record_id
- L2 raw_record_version_id

Provenance must support N:M relationships.

### Algorithm Definition

Every generated L3 result must identify:

- definition_id
- definition_version

---

## 6. Time Contract

TIME-001
All point timestamps and interval boundaries use UTC internally.

TIME-002
Original timezone_name and timezone_offset are preserved.

TIME-003
Daily facts use local_date and do not invent an event timestamp.

TIME-004
Bucket facts initially use a vendor anchor timestamp.
Bucket width must not be invented before validation.

TIME-005
Interval facts preserve start and end boundaries.

TIME-006
Sleep Session analysis date defaults to wake local date.

TIME-007
Daily features are grouped according to the timezone context of the source fact.

TIME-008
Historical facts must not move between calendar dates when the user's current timezone changes.

TIME-009
Analysis-date fields do not replace real timestamps.

---

## 7. Revision-Aware Processing

Layer 2 observation semantics:

- NEW
- REVISION
- REOBSERVATION

L3 behavior:

REOBSERVATION:
- no downstream recomputation by default

NEW:
- generate new normalized fact
- determine affected downstream windows
- recompute only affected results

REVISION:
- invalidate normalized / derived state based on the superseded source version
- normalize the new source version
- recompute affected downstream windows

Rules:

- Processing checkpoints are based on L2 change progression, not health event time alone.
- Historical revisions must be discoverable.
- Feature definitions must declare dependencies and impact windows.
- Incremental recomputation should use the minimum affected window.
- Derived states may be CURRENT, STALE, or FAILED.
- Only successfully recomputed results may become CURRENT.
- Definition-version changes can also trigger invalidation.
- Full rebuild from L2 is always supported.

---

## 8. Quality Contract

Evidence type and data quality are independent.

v0.1 does not use a single opaque global quality score.

Quality is represented using explicit evidence and flags.

Initial quality concepts:

- SPARSE
- GAP
- PARTIAL_COVERAGE
- SOURCE_CONFLICT
- OFF_WRIST_SUSPECTED

Rules:

- Metric-specific thresholds must be versioned.
- OFF_WRIST is only a suspicion unless directly observed.
- Source revision does not automatically mean poor quality.
- Quality flags must record rule_id and rule_version.
- Sleep data quality and sleep inference confidence are separate concepts.
- Structurally complete vendor output does not imply physiological ground truth.

---

## 9. Source Resolution Contract

L3A preserves all source facts.

L3B performs source resolution.

Supported resolution semantics:

- SINGLE_SOURCE
- AGREE
- COMPLEMENTARY
- CONFLICT
- RESOLVED
- UNRESOLVED

Rules:

- No global source priority is allowed across all metrics.
- Source priority must be metric-specific, context-specific, and versioned.
- Component-level source resolution is allowed.
- Multi-source values must not be silently averaged, added, overwritten, or deleted.
- UNRESOLVED is a valid result.
- Canonical results must preserve membership and resolution provenance.
- A selected vendor source remains vendor-derived/inferred and does not become ground truth.

---

## 10. Heart Rate Contract v0.1

Dataset:
heart_rate

Validated raw versions:
2769

Observed structure:

value:
- time
- bpm
- type

Validated:

- parse errors = 0
- missing bpm = 0
- missing time = 0
- inner time mismatch = 0
- type = 0 for all current samples
- source SID = 896085753 for all current samples

Normalized semantics:

- metric = heart_rate
- temporal_type = POINT
- evidence_type = SENSOR_DERIVED
- event_time = value.time
- value = value.bpm
- unit = bpm

Raw type code is preserved but its meaning is not assumed.

Observed BPM range is not used as an abnormality threshold.

---

## 11. Resting Heart Rate Contract v0.1

Dataset:
resting_heart_rate

Current sample count:
2

Validated structure:

value:
- bpm
- date_time

Current records use UTC 00:00 as the date anchor and convert to 08:00 in Asia/Shanghai.

This must not be interpreted as an actual 08:00 measurement.

Normalized semantics:

- metric = resting_heart_rate
- temporal_type = DAILY
- local_date = UTC calendar date represented by date_time
- value = bpm
- unit = bpm

Current semantics are validated for available data and may be versioned if new evidence contradicts them.

---

## 12. Sleep Contract v0.1

Sleep is not modeled as one Xiaomi record = one night.

Canonical conceptual structure:

Canonical Sleep Session
├─ 0..N Generated Source Episodes
├─ 0..N Numeric-SID / wearable-associated Source Episodes
└─ 0..N Sleep Segments

### Numeric SID stage mapping

Verified from real duration equality:

- state 2 = DEEP
- state 3 = LIGHT
- state 4 = REM
- state 5 = AWAKE

This mapping produced zero duration error in current staged records.

### Generated source mapping

Verified:

- state 5 = AWAKE
- state 6 = SLEEP

### Important evidence semantics

Xiaomi sleep stages and sleep/awake states are vendor algorithm inferences.

They are NOT treated as physiological ground truth.

A vendor result such as LIGHT, DEEP, REM, AWAKE, or SLEEP means:

"Xiaomi algorithm classified this interval as ..."

It does not mean the user's true physiological state is known.

### Multi-episode behavior

Real data confirms one night may contain:

- one generated episode
- multiple numeric-SID sleep episodes

Therefore one night must not be assumed to equal one source record.

### Source semantics

Generated sleep provides:
- coarse SLEEP / AWAKE trace
- useful session-coverage evidence

Numeric-SID sleep provides:
- DEEP / LIGHT / REM / AWAKE stages
- HR statistics
- SpO2 statistics
- breathing statistics
- sleep algorithm metadata when available

The sources may be complementary.

No destructive winner-takes-all rule is allowed.

Exact canonical sleep duration, canonical awake intervals, and final source thresholds are deferred until sufficient evidence exists.

---

## 13. SpO2 Contract v0.1

Dataset:
spo2

Validated raw versions:
388

Validated:

- parse errors = 0
- missing value = 0
- missing inner time = 0
- time mismatch = 0
- source SID = 896085753 for all current samples

Normalized semantics:

- metric = spo2
- temporal_type = POINT
- evidence_type = SENSOR_DERIVED
- event_time = value.time
- value = value.spo2
- unit = percent

Observed range does not define health or validity thresholds.

---

## 14. Xiaomi Stress Contract v0.1

Dataset:
stress

Validated raw versions:
485

Validated structure:

value:
- time
- stress

Normalized semantics:

- metric = xiaomi_stress_score
- temporal_type = POINT
- evidence_type = VENDOR_DERIVED
- event_time = value.time
- value = value.stress
- unit = vendor_score

The Xiaomi stress score is not treated as:

- direct physiological measurement
- psychological stress ground truth
- medical stress assessment

---

## 15. Steps Contract v0.1

Dataset:
steps

Validated structure:

value:
- time
- steps
- distance
- calories

Two source families are currently present:

- numeric SID
- hlth.gen_* generated source

They may coexist at the same timestamp and usually contain different values.

Therefore:

- source facts are preserved independently
- values from different sources must not be directly summed
- L3A does not choose a winning source

### Generated embedded calories

Verified:

generated steps.calories = steps * 0.04

for all current latest generated step records, within floating-point precision.

This is therefore treated as a deterministic vendor-derived, step-linked calorie estimate.

### Numeric-SID embedded calories

When numeric steps.calories and standalone numeric calories coexist at the same timestamp, current data shows equality.

The underlying calorie algorithm remains unknown.

### Temporal semantics

Steps are treated as bucketed/activity facts rather than instantaneous point measurements.

Current raw timestamp is retained as bucket anchor time.

Exact bucket width is not assumed until validated.

---

## 16. Calories Contract v0.1

Dataset:
calories

Validated raw versions:
1131

Validated structure:

value:
- time
- calories

Sources:

- numeric SID
- hlth.gen_* generated source

Normalized semantics:

- metric = calories
- temporal_type = BUCKET
- bucket anchor = value.time
- value = value.calories
- source preserved

The exact vendor meaning of standalone calories is not over-interpreted.

It is not automatically renamed as:

- total calories
- activity calories
- walking calories

without additional evidence.

Standalone calories and steps.embedded_calories must not be blindly deduplicated across all source types.

---

## 17. Rebuild Contract

The following must always hold:

delete entire L3
+
preserve L2
+
preserve L3 code / definitions
=
complete L3 rebuild

Incremental processing is an optimization.

L2 remains the recovery source of truth.

---

## 18. Core Acceptance Criteria v0.1

Layer 3 Core Acceptance must at minimum validate:

01 SQLite integrity
02 Foreign keys
03 L3 / L2 database isolation
04 Production schema version

05 Heart rate normalization
06 Resting HR normalization
07 Sleep normalization
08 SpO2 normalization
09 Stress normalization
10 Steps normalization
11 Calories normalization

12 Temporal semantics
13 Evidence-type semantics
14 Full provenance traceability

15 Source coexistence
16 Quality rule provenance

17 NEW incremental processing
18 REVISION invalidation / recompute
19 REOBSERVATION no-op semantics

20 L2-only full rebuild equivalence

Layer 3 must not be marked SEALED until the implementation passes formal acceptance.

---

## 19. Current Layer Status

Layer 1 Data Acquisition
STATUS = SEALED

Layer 2 Raw Health Data Store
STATUS = SEALED

Layer 3 Feature Engineering
ARCHITECTURE & FEATURE CONTRACT v0.1 = SEALED
IMPLEMENTATION = SEALED

Layer 4 Personal Baseline
STATUS = NOT STARTED

Layer 5 Health Analytics
STATUS = NOT STARTED

Layer 6 AI Reasoning
STATUS = NOT STARTED

Layer 7 Product Output
STATUS = NOT STARTED
