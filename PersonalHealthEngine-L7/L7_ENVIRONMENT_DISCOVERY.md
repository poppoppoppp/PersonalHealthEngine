# L7 Environment Discovery (Phase A)

Status: COMPLETE
Date (local, +08:00): 2026-08-17
Method: direct inspection of `D:\PersonalHealthEngine-*`, all SEAL/HANDOFF/CONTRACT/AUDIT
documents, all SQLite schemas (read-only `mode=ro`), all L6 scripts, and live runtime checks.
Nothing in this document is guessed; every item cites the file/table it was read from.

---

## 1. Upstream directories found

| Directory | Layer | State evidence |
|---|---|---|
| `D:\PersonalHealthEngine-L1Lab` | L1 Data Acquisition | `xiaomi-raw-collector\L1_SEAL.md` |
| `D:\PersonalHealthEngine-L2` | L2 Raw Health Data Store | `L2_SEAL.md`, `L2_FINAL_AUDIT.json` |
| `D:\PersonalHealthEngine-L3` | L3 Feature Engineering | `L3_SEAL.md`, `L3_CONTRACT.md`, `L3_HANDOFF.md`, `L3_FINAL_AUDIT.json` |
| `D:\PersonalHealthEngine-L4` | L4 Personal Baseline | `L4_SEAL.md`, `L4_CONTRACT.md`, `L4_HANDOFF.md`, `L4_FINAL_AUDIT.json` |
| `D:\PersonalHealthEngine-L5` | L5 Health Analytics | `L5_SEAL.md`, `L5_CONTRACT.md`, `L5_HANDOFF.md`, `L5_FINAL_AUDIT.json` |
| `D:\PersonalHealthEngine-L6` | L6 AI Reasoning | `L6_SEAL.md`, `L6_CONTRACT.md`, `L6_HANDOFF.md`, `L6_FINAL_AUDIT.json`, `REAL_MODEL_INTEGRATION.json` |
| `D:\PersonalHealthEngine-L7` | L7 Product Output | this workspace (was empty) |

Canonical layer status confirmed from `L6_SEAL.md`:
`L1..L5 = SEALED, L6 = SEALED (CORE), L7 = NOT STARTED`.

Real-model state (supersedes the earlier `FALSE` note inside `L6_SEAL.md`):
`REAL_MODEL_INTEGRATION.json` (2026-08-17T10:14:15Z) declares
`REAL_DEEPSEEK_INTEGRATION_VERIFIED = TRUE`, `REAL_MEDGEMMA_INTEGRATION_VERIFIED = TRUE`,
`REAL_MODEL_INTEGRATION_VERIFIED = TRUE` — 7 DeepSeek cases PASS, 6 MedGemma cases PASS.

---

## 2. Databases and schemas (all inspected read-only)

### 2.1 L2 raw store — `D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3`

Schema v1 (`schema_migrations`), WAL, append/version-preserving (`L2_SEAL.md`).
Tables: `captures` (9), `source_artifacts` (81), `logical_records` (5030),
`raw_record_versions` (5297), `raw_record_observations` (15751), `ingestion_runs`,
`ingestion_run_artifacts`, `ingestion_issues`, `schema_migrations`.

- Frozen logical identity: `provider + region + dataset + raw_key + raw_sid + raw_time`
  (`identity_version = 'xiaomi-v0.1'`).
- Captures span data dates **2026-08-10 .. 2026-08-16**, last capture 2026-08-16T02:57Z.
- Immutable archive: `D:\PersonalHealthEngine-L2\archive` (source of full rebuild).

### 2.2 L3 features — `D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3`

Schema v8. Tables: `fact_registry` (5179), `normalized_point_facts` (3482),
`normalized_bucket_facts` (1532), `normalized_interval_facts` (163),
`normalized_daily_facts` (2), `derived_features` (229), `quality_assessments` (15537),
`source_resolution_decisions` (4994), provenance/input link tables, `pipeline_runs`,
`processing_checkpoints`, `definition_registry`, `normalization_issues`.
Feature dates: **2026-08-10 .. 2026-08-16**.

Metrics present: `heart_rate`, `resting_heart_rate`, `spo2`, `xiaomi_stress_score`,
`calories`, `steps`, `sleep` (source-episode scope, vendor stage attributes).

### 2.3 L4 baselines — `D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3`

Schema v2. Tables: `baseline_series` (44), `rolling_baselines` (1056) with
`window_days`, `mean/median/mad/q10/q25/q50/q75/q90`, maturity
(`INSUFFICIENT_HISTORY | PROVISIONAL | ESTABLISHED`), `baseline_feature_inputs`,
`baseline_input_state`, `pipeline_runs`, `processing_checkpoints`, `definition_registry`.
Latest `as_of_date`: **2026-08-17**.

### 2.4 L5 analytics — `D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3`

Schema v2. Tables: `analytics_series` (44), `deviation_analytics` (687; robust z / MAD /
quantile position / deviation_class / evidence_status), `persistence_analytics` (72),
`trend_analytics` (24; Theil-Sen + Spearman), `change_point_analytics` (24),
`relationship_analytics` (4), input link tables, `pipeline_runs`,
`processing_checkpoints`, `definition_registry`, `upstream_input_state`.
Deviation dates: **2026-08-10 .. 2026-08-16**.

### 2.5 L6 reasoning — `D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3`

Schema v2 (migrations `001_foundation.sql`, `002_reasoning_core.sql`). Tables:
`personal_context` (2 CURRENT), `context_revisions`, `user_feedback` (1),
`evidence_bundles` (1), `hypotheses` (1), `daily_reasoning` (1), `qa_sessions` (1),
`medical_reviews` (1), `model_invocations` (1), `personal_patterns` (12, all OBSERVING),
`reasoning_provenance` (256), `pipeline_runs`, `processing_checkpoints`,
`definition_registry` (7 registered definitions), `reasoning_issues`, `schema_migrations`.

Current CURRENT daily reasoning (id 1, sealed production row):

```text
analysis_date            = 2026-08-16
overall_state            = NOTABLE_CHANGE
primary_hypothesis_type  = SLEEP_DEFICIT
secondary                = null
confidence               = LOW
medical_review_state     = BYPASSED
reasoning_model          = mock-reasoning-v0.1
recommended_actions      = [今天优先补充睡眠, 暂缓高强度训练, 避免咖啡因在下午之后摄入]
```

Enums (CHECK constraints, verified in schema):

```text
overall_state ∈ {STABLE, MILD_CHANGE, NOTABLE_CHANGE, INSUFFICIENT_EVIDENCE}
confidence    ∈ {VERY_LOW, LOW, MODERATE, HIGH}
hypothesis types (v0.1): RECOVERY_STRAIN, SLEEP_DEFICIT, STRESS_RESPONSE,
                         ACUTE_ILLNESS_SUSPECTED, NO_SIGNIFICANT_FINDING, UNKNOWN
medical_review_state ∈ {REQUIRED, PERFORMED, BYPASSED, UNAVAILABLE}
context_type vocabulary (l6_core): HIGH_INTENSITY_TRAINING, ALCOHOL_USE, LATE_SLEEP,
  CAFFEINE, STRESS, TRAVEL, FEVER, SORE_THROAT, NASAL_CONGESTION, MEDICATION,
  FATIGUE, FEELING_GOOD, DIET_CHANGE, SCHEDULE_CHANGE, ILLNESS
```

Four information classes are enforced by schema
(`personal_context.source CHECK = 'USER_REPORTED'`, `user_feedback.source CHECK = 'USER_FEEDBACK'`,
hypotheses/daily_reasoning = AI_INFERENCE).

---

## 3. Reusable libraries / modules (read-only reuse, no modification)

All L6 code is plain Python modules under `D:\PersonalHealthEngine-L6\scripts`:

| Module | What L7 reuses |
|---|---|
| `l6_core_v0_1.py` | `overall_state()`, `generate_candidates()`, `base_confidence()`, `medical_trigger()`, `validate_daily_output()`, `canonical_json()`, `sha256_text()`, enums, `extract_context_events()` |
| `l6_evidence_v0_1.py` | `assemble_evidence()` (deterministic Evidence Bundle + provenance), `bundle_sha256()` |
| `l6_adapters_v0_1.py` | `MockReasoningModelAdapter`, `MockMedicalModelAdapter` (deterministic dev/test path), adapter protocols |
| `l6_real_adapters_v0_1.py` | `RealDeepSeekReasoningModelAdapter` (`reason_daily`, `answer_question`, `extract_context`), `RealMedGemmaMedicalModelAdapter` (`review`, `verify_model_identity`) |
| `l6_reasoning_materializer_v0_1.py` | `register_definition()`, `reconcile_daily()` (idempotent, STALE-then-insert versioning), `read_recent_context()`, `similar_cases()` |

Reuse mechanism: L7 adds `D:\PersonalHealthEngine-L6\scripts` to `sys.path` and imports.
No L6 file is edited. This is the sanctioned adapter/service-boundary access.

Upstream incremental pipeline scripts (needed by the L7 scheduler, invoked as CLIs, never edited):

```text
L2 import:   D:\PersonalHealthEngine-L2\scripts\import_l1_captures.py
L3 runners:  l3_point_incremental_runner_v0_1.py, l3_bucket_incremental_runner_v0_1.py,
             l3_sleep_incremental_runner_v0_1.py, incremental_heart_rate_v0_1.py,
             incremental_resting_heart_rate_v0_1.py
L4:          l4_baseline_materializer_v0_1.py
L5:          l5_analytics_materializer_v0_1.py
L6:          l6_reasoning_materializer_v0_1.py (mock), l6_context_ingest_v0_1.py,
             l6_feedback_v0_1.py, l6_qa_v0_1.py
```

---

## 4. L7 read boundaries (what L7 may read, and how)

1. **L6 db (primary)** — read CURRENT `daily_reasoning`, `hypotheses`, `evidence_bundles`,
   `medical_reviews`, `personal_context`, `user_feedback`, `personal_patterns`,
   `qa_sessions`, `reasoning_provenance`, `model_invocations`. SQLite `mode=ro`.
2. **L5 db** — read-only, Evidence Level-3 drill-down (deviation/persistence/trend detail,
   dates, values) for the "查看依据" path and History episodes.
3. **L4 db** — read-only, baseline detail (median/MAD/quantiles, maturity, window) for
   Evidence Level 3.
4. **L3 db** — read-only, raw feature values/trend series for charts in Evidence Level 3.
5. **L2 db** — read-only and only for provenance display (capture/date range); L7 never
   re-parses raw payloads.
6. **L6 code modules** — import-only (section 3).

L7 **writes** to:

- its own new L7 product database (Today versions, conversations, notification decisions,
  settings, episodes projection cache);
- the L6 db **only through sealed L6 entry points** (context ingest, feedback, QA,
  materializer functions) — same write discipline L6 itself uses.

---

## 5. SEALED boundaries L7 must not touch

- Never modify any file or schema in L1Lab, L2, L3, L4, L5, L6 (code, databases,
  definitions, migrations).
- Never run FULL_REBUILD against any production upstream db from L7; incremental modes only.
- Never re-derive L3-L5 analytics or baseline semantics in L7 (no scoring, no re-baselining).
- Never bypass the four information classes (AI inference must never be written back as
  USER_REPORTED / USER_FEEDBACK).
- Never persist model credentials; `DEEPSEEK_API_KEY` comes from environment variables only
  (it exists in the Windows **User** environment store on this machine; not committed anywhere).
- Never store or surface MedGemma thinking traces.

---

## 6. Runtime facts (verified live on 2026-08-17 21:2x +08:00)

```text
OS date                 = 2026-08-17
Python                  = 3.14.4 (system), 3.12 venv in L1Lab
pip (system python)     = fastapi 0.115.0, uvicorn 0.30.6, pydantic 2.13.4,
                          httpx 0.28.1, pytest 9.0.3  -> sufficient for L7 backend
Flutter                 = D:\flutter\flutter (dart on PATH)
JDK                     = 17 (Microsoft OpenJDK)
Android SDK             = %LOCALAPPDATA%\Android\Sdk (exists)
Node                    = present
Docker                  = NOT installed (Phase H must account for this)
Ollama HTTP             = http://localhost:11434 UP; serving medgemma1.5:latest
                          digest 433252621ab1... matches REAL_MODEL_INTEGRATION.json
DEEPSEEK_API_KEY        = present in User env store (not in transient pwsh process env)
Data freshness          = latest sensor evidence 2026-08-16; L4 baselines as-of 2026-08-17
L1 collection           = xiaomi-raw-collector run_daily_collector.ps1 (scheduled capture),
                          captures land in ...\xiaomi-raw-collector\captures, then
                          L2 import script ingests them
```

---

## 7. Findings that shape the L7 design (no blocking conflicts)

1. **Real-adapter materialization gap.** The sealed `l6_reasoning_materializer_v0_1.py`
   accepts `--reasoning-adapter {mock,deepseek}`, but the sealed
   `DeepSeekReasoningModelAdapter` in `l6_adapters_v0_1.py` is a stub that raises
   `ModelError("not configured")`. The real adapter lives in `l6_real_adapters_v0_1.py`
   and was only wired to the standalone integration report. Therefore **the current
   production `daily_reasoning` row was produced by the mock adapter**, and no sealed CLI
   today materializes *real* DeepSeek reasoning into the L6 production db.
   - Resolution (no upstream change): L7 implements an orchestration module that imports
     the sealed L6 functions (`assemble_evidence`, `generate_candidates`,
     `base_confidence`, `validate_daily_output`, `medical_trigger`, `reconcile_daily`,
     definition loaders) plus `RealDeepSeekReasoningModelAdapter` /
     `RealMedGemmaMedicalModelAdapter`, reproducing the materializer flow exactly with the
     real adapters. All writes still go through `reconcile_daily()` semantics
     (hash-idempotent; old rows STALE, never deleted). Mock adapters remain the default for
     tests and deterministic replay.
   - This is a new L7-owned integration seam, not a modification of SEALED core.

2. **Today versioning is already append-only upstream.** `reconcile_daily()` marks old
   `evidence_bundles` / `hypotheses` / `daily_reasoning` rows STALE and inserts new CURRENT
   rows when the bundle hash changes; identical bundles are no-ops. L7 Today versioning is a
   thin projection over this (`bundle_sha256` + reasoning id) plus L7's own rendered-copy
   versions. No destructive updates anywhere.

3. **Deterministic change-detection anchor exists.** `bundle_sha256` (canonical JSON) is the
   exact key for "did evidence meaningfully change" → Recompute Threshold. L7 adds a second
   deterministic signature over the *presented* judgment for the UI Change Threshold and a
   stricter value test for the Notification Threshold — the three thresholds stay separate.

4. **Q&A seam exists but is mock-only.** `l6_qa_v0_1.py` builds the Question Evidence Bundle
   deterministically; L7 reuses `assemble_evidence` + `generate_candidates` +
   `medical_trigger` and calls `RealDeepSeekReasoningModelAdapter.answer_question` under the
   same medical policy. Conversation lifecycle (rollover, budget) is an L7 concern.

5. **Context capture seam exists.** `l6_context_ingest_v0_1.py` stores USER_REPORTED context
   with CORRECTION/DELETION revisions (SUPERSEDED, never overwritten) — exactly the Fact
   Priority semantics the contract requires. L7 adds natural-language capture via
   `RealDeepSeekReasoningModelAdapter.extract_context` (falling back to the deterministic
   keyword extractor) and time-semantics metadata in the L7 db.

6. **Pattern projection is cheap.** `personal_patterns` already stores
   support/total counts, first/last seen, maturity — L7's "我的规律" filters to
   actionable/ESTABLISHED patterns and renders accumulation state; counter-evidence
   rendering uses support vs total. No new pattern learning is needed in MVP beyond the
   existing L6 loop.

7. **No credential problem.** DeepSeek key is in the user env store; Ollama is up with the
   verified MedGemma digest. L7 backend loads env at startup; nothing is written to repo/db.

8. **Cost posture.** Evidence bundles are small structured JSON (the sealed bundle for
   2026-08-16 is a few KB); raw time series never go to the model. Real-call evidence from
   `REAL_MODEL_INTEGRATION.json`: daily reasoning ≈ 11-30 s latency, QA `reasoning_effort`
   is already forced to `low`. Hash-keyed reuse + change detection keeps paid calls minimal.

**Conclusion: no contract conflict blocks Phase C. Proceeding.**
