# Personal Health Engine
# Layer 6 Architecture & AI Reasoning Contract v0.1

Status: ACTIVE (implementation in progress)
Date: 2026-08-16
Layer: Layer 6 = AI Reasoning

---

## 1. Layer Boundary

Layer 6 answers, in plain language and grounded in this user's own data:

1. How is the user overall today?
2. What most likely happened recently?
3. Why might that be?
4. What should the user concretely do today?
5. Answer the user's actual health questions with their personal data.

Layer 6 is NOT "health data + a chatbot". It is a deterministic, provenance-aware reasoning
pipeline that only then invokes a reasoning model over a compressed, structured Evidence
Bundle. The primary production model is DeepSeek V4 Pro behind a model-independent adapter;
a Medical reviewer (MedGemma 1.5 4B) is invoked on demand behind its own adapter. Both are
replaceable without changing L6 core logic.

---

## 2. Upstreams (all read-only, all SEALED)

- L3 features: D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3 (schema v8)
- L4 baselines: D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3 (schema v2)
- L5 analytics: D:\PersonalHealthEngine-L5\db\personal_health_analytics.sqlite3 (schema v2)

L6 never modifies L1-L5, never re-queries Xiaomi Cloud, never re-parses L2, and never redoes
L3-L5 work.

---

## 3. Storage Architecture

Canonical L6 root: D:\PersonalHealthEngine-L6

Canonical L6 database: D:\PersonalHealthEngine-L6\db\personal_health_reasoning.sqlite3

L6 is derived, materialized, and rebuildable state. Deleting L6 while preserving upstreams and
L6 code/definitions must allow a full, deterministic replay.

---

## 4. Four Information Classes (hard rule)

Every stored fact is exactly one of:

- SENSOR / UPSTREAM FACT — data and analytics from L1-L5.
- USER_REPORTED_CONTEXT — what the user explicitly told the system.
- AI_INFERENCE — what the system inferred (hypotheses, daily reasoning, answers).
- USER_FEEDBACK — user confirmation/correction of an AI judgment.

AI inference is NEVER written back as user fact. A model output is stored only as
`AI_INFERENCE` (or `USER_FEEDBACK` when the user endorses/corrects it). This prevents the
"AI guesses -> memory -> treats own guess as evidence" loop.

---

## 5. Personal Context

Natural-language life events are ingested and structured. The raw text is retained (minimally),
and the structured extraction carries `source = USER_REPORTED`. The extractor must NOT invent
fields the user did not say (e.g. "slept late" must not become `sleep_duration=5h`).

Context corrections produce formal revisions (CORRECTION / DELETION) with provenance; a
corrected context is SUPERSEDED, never silently overwritten.

---

## 6. Evidence Bundle (deterministic)

Code assembles a structured Evidence Bundle before any model runs. The model never browses the
database itself. A bundle contains: analysis date, key L5 deviations, persistence, trends,
change results, relationships, baseline maturity, evidence strength, recent Personal Context,
recent feedback, relevant similar historical cases, and important missing evidence. The bundle
is hash-addressed and carries provenance to L3/L4/L5 inputs.

---

## 7. Hypothesis Framework

The core reasoning unit is a Hypothesis with: `hypothesis_type`, `supporting_evidence`,
`counter_evidence`, `missing_evidence`, `confidence`, `reasoning_summary`. The deterministic
core generates candidates with supporting evidence; the model ranks them and must actively
surface counter and missing evidence. The default user view shows only a primary (and at most
one secondary) hypothesis, never a long list of medical possibilities.

Hypothesis types (v0.1): RECOVERY_STRAIN, SLEEP_DEFICIT, STRESS_RESPONSE,
ACUTE_ILLNESS_SUSPECTED, NO_SIGNIFICANT_FINDING, UNKNOWN.

---

## 8. Overall State (no health score)

`overall_state` ∈ {STABLE, MILD_CHANGE, NOTABLE_CHANGE, INSUFFICIENT_EVIDENCE}, derived
deterministically from evidence, never invented by the model. There is NO health/readiness/
recovery/wellness/body-age score and no 0-100 composite score.

Stable day: output "stable, keep to plan" — never manufacture advice or warnings.

---

## 9. Confidence

Confidence is a transparent enum {VERY_LOW, LOW, MODERATE, HIGH}, never a fake percentage. It is
computed by deterministic rules from baseline maturity, L5 evidence strength, supporting/
counter evidence, missing evidence, and context/pattern support. The model may only explain or
downgrade, never upgrade, the deterministic base confidence.

---

## 10. Action Recommendations

Actions must be actionable and correspond to the primary hypothesis + evidence. v0.1 avoids
empty advice ("rest", "drink water") unless the reasoning actually supports it. Examples:
continue normal training, reduce intensity, postpone high-intensity work, prioritize sleep,
observe 24-48h, measure temperature, add a data source, or seek medical evaluation when
warranted.

---

## 11. Medical Boundary & Review Policy

L6 is a personal health reasoning assistant, not a clinical diagnosis system. It never
diagnoses a disease. A deterministic medical-review policy triggers a Medical reviewer when:
user-reported symptoms, disease/drug questions, persistent notable anomalies, high-risk
hypotheses, "should I see a doctor" questions, or safety-rule hits. Low-risk, stable scenarios
bypass the reviewer. Reviewer results are MEDICAL_REVIEW states; if the reviewer is
unavailable the state is MEDICAL_REVIEW_UNAVAILABLE and the deterministic L5 evidence is
retained (no fabricated medical conclusion).

---

## 12. Model Strategy (model-independent)

- `ReasoningModelAdapter` protocol: extract_context, reason_daily, answer_question.
  - `MockReasoningModelAdapter` (deterministic; used by acceptance/rebuild)
  - `DeepSeekReasoningModelAdapter` (config-driven; NOT exercised by acceptance)
- `MedicalModelAdapter` protocol: review.
  - `MockMedicalModelAdapter` (deterministic)
  - `MedGemmaMedicalModelAdapter` (local or remote HTTPS endpoint; NOT exercised by acceptance)

All model outputs are validated against a schema; invalid/timeout/error responses fail safely
(REASONING_UNAVAILABLE / MEDICAL_REVIEW_UNAVAILABLE) and never corrupt the reasoning tables.
API keys are never persisted, never logged, never enter the database.

---

## 13. Personal Pattern (foundation)

Long-term feedback learning stores Personal Patterns of the form
`trigger_context_type -> outcome_signal` with support/total counts, first/last seen dates, and
maturity. v0.1 requires a support threshold (>= 3 independent cases) before a pattern is
`ESTABLISHED`; below that it is `OBSERVING`. Patterns are statistical/experiential only —
never causal ("past N of M times ... happened" is allowed; "X always causes Y" is not).
Patterns never form from a single AI guess; they require observed outcomes plus user feedback
or structured fact.

---

## 14. Deterministic vs LLM Boundary

Deterministic code owns: data selection, time windows, upstream joins, source semantics,
evidence strength, no-look-ahead, provenance, base confidence, overall state, medical trigger
policy, output/schema validation, safety constraints, credential isolation. The LLM owns:
context synthesis, hypothesis interpretation/ranking, plain-language explanation, contextual
action suggestions, and interactive Q&A. The LLM never takes over deterministic data
engineering.

---

## 15. No Look-Ahead & Replay

A reasoning for date D uses only data and context available on/before D. Deterministic replay
reproduces "what the system would have concluded then". Future data may only be used for later
evaluation/feedback, never to rewrite past reasoning.

---

## 16. Q&A

Interactive questions are answered only after a deterministic Question Evidence Bundle is
assembled (current state, recent L5 analytics, L4 baselines, recent context, relevant
patterns, recent feedback, and medical knowledge trigger). Answers are grounded in personal
data; no diagnosis without appropriate evidence and review.

---

## 17. Versioned Definitions

All key logic is versioned and SHA-256 registered: context extraction, evidence assembly,
hypothesis framework, confidence policy, daily reasoning, medical review policy, personal
pattern policy.

---

## 18. Acceptance Scope

Core acceptance (mock adapters only, no paid API) must cover: upstream read-only / L3-L5
unmodified, schema integrity, migration chain, definition checksums, 4-way source distinction,
AI-inference-not-promoted-to-fact, feedback provenance, context revision, deterministic
evidence bundle, no look-ahead, hypothesis supporting/counter/missing evidence, deterministic
confidence constraints, insufficient-evidence handling, stable-day behavior, primary
hypothesis selection, no-hypothesis-when-insufficient, personal-pattern threshold + non-causal,
DeepSeek adapter presence, mock adapter, MedGemma adapter interface, medical trigger +
low-risk bypass, structured-output validation, invalid-response safety, timeout safety, no
credential persistence, Q&A bundle + grounding, no diagnosis without review, no health score,
source/vendor semantics preserved, missing != zero, no canonical sleep night, model-independent
architecture, full rebuild, deterministic replay, incremental/revision behavior, final audit.

Real DeepSeek/MedGemma integration is a separate smoke test and is NOT required for seal.
Sealing L6 CORE does not assert REAL MODEL INTEGRATION VERIFIED.

---

## 19. Current Layer Status

- L1 Data Acquisition: SEALED
- L2 Raw Health Data Store: SEALED
- L3 Feature Engineering: SEALED
- L4 Personal Baseline: SEALED
- L5 Health Analytics: SEALED
- L6 AI Reasoning: IN DEVELOPMENT
- L7 Product Output: NOT STARTED
