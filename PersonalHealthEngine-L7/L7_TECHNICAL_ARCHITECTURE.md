# L7 Technical Architecture (Phase B)

Status: APPROVED-FOR-IMPLEMENTATION
Date: 2026-08-17
Inputs: `L7_ENVIRONMENT_DISCOVERY.md`, the approved Layer 7 Product Contract,
`D:\PersonalHealthEngine-L6\L6_CONTRACT.md`, all SEALED layer documents.

L7 does not re-reason. It projects, versions, presents, schedules and guards the
intelligence that L1–L6 already produce.

---

## 1. Technology choices (with reasons)

### 1.1 Backend: Python 3 + FastAPI + SQLite(WAL)

- L1–L6 are Python + SQLite. Reusing the sealed L6 modules (import-only) is only possible
  from Python; any other stack would force re-implementing deterministic evidence
  assembly — forbidden.
- `fastapi`, `uvicorn`, `pydantic`, `httpx`, `pytest` are already installed for the system
  Python 3.14. No new dependency risk.
- FastAPI gives: async endpoints (model calls are slow), structured JSON contracts,
  OpenAPI for the mobile client, background tasks for re-evaluation.
- Scheduler: a single in-process asyncio loop with deterministic tick jobs (no APScheduler
  dependency needed for MVP; the loop is a plain async task so it ports to any cloud
  runner/container unchanged).

### 1.2 Mobile: Flutter (Android first)

- Flutter + Dart are installed (`D:\flutter\flutter`), JDK 17 + Android SDK present —
  the first runnable mobile build costs zero new setup.
- Android-first for the Xiaomi Band 9 owner; iOS path stays open (Flutter compiles to iOS
  without re-architecture). Native Android (Kotlin) would close the iOS path; React Native
  would add a JS toolchain that nothing else in this project uses.
- Flutter local cache: a thin SQLite store on-device holding the last Product API payloads
  (Today snapshot, evidence detail, patterns) so the app opens instantly offline.
- Notifications: MVP uses in-app notification feed + local notifications; push
  infrastructure is deferred (Phase G boundary documented). The backend Notification
  Service logic (thresholds, modes) is transport-independent.

### 1.3 Database: keep SQLite; PostgreSQL only on proven need

- Single user, one writer (backend), moderate volume (thousands of rows/day at most).
  SQLite WAL preserves every provenance/version semantic the upstream layers established
  and keeps backup trivial (`VACUUM INTO` + archive copy).
- A Postgres migration buys concurrent writers/HA — neither exists in the single-user MVP.
  Migration path stays open: L7 persistence goes through a small repository interface
  (`l7/store`), all DDL in versioned migrations; no SQLite-only syntax leaks into domain
  logic. Decision rule for switching: multi-user rollout or background workers that must
  write concurrently with the API.
- L7 never writes into upstream dbs except **through sealed L6 entry points** (context
  ingest, feedback, QA, materializer functions).

---

## 2. System shape

```text
                ┌────────────────────────── Mobile (Flutter) ─────────────────────────┐
                │ 今日 / 历史 / 我的规律 / 我的  +  Q&A page  +  Context input        │
                │ local cache (last payloads) · auth token · no secrets, no reasoning │
                └───────────────▲──────────────────────────────────────────────────────┘
                                │ HTTPS JSON (Product API)
┌───────────────────────────────┴───────────────────────────────────────────────────────┐
│ L7 Backend (FastAPI, single deployable)                                                │
│                                                                                        │
│  Product API (routers)                                                                 │
│   /today  /today/versions  /evidence/{..}  /qa  /context  /feedback                    │
│   /history/episodes  /patterns  /notifications  /settings                              │
│                                                                                        │
│  Services (domain logic, all server-side)                                              │
│   TodayService · QnAService · ContextService · FeedbackService                         │
│   HistoryService(episode projection) · PatternProjection · NotificationService         │
│   EngineOrchestrator (the only component that triggers L6 reasoning runs)              │
│                                                                                        │
│  Scheduler loop (asyncio ticks): data-readiness check · open-app check (API-driven)    │
│   · context/feedback-triggered re-evaluation · conversation rollover · daily summary   │
│                                                                                        │
│  Persistence: L7 product db (SQLite WAL) + read-only upstream access layer             │
└───────▲────────────────────────────────────────────────────────────────────────────────┘
        │ import-only reuse (sys.path, no edits)                │ mode=ro SQLite reads
┌───────┴────────────────────────┐               ┌──────────────┴──────────────────────┐
│ SEALED L6 modules              │               │ L3 / L4 / L5 dbs (Evidence L3       │
│ l6_core, l6_evidence,          │               │ drill-down), L2 db (provenance      │
│ l6_adapters (mock),            │               │ display only)                       │
│ l6_real_adapters (DeepSeek /   │               └─────────────────────────────────────┘
│ MedGemma), materializer funcs  │
└────────────────────────────────┘
        │ writes only through sealed entry points (reconcile_daily, context ingest, feedback)
        ▼
  L6 reasoning db (append-only versions)
```

DeepSeek API and MedGemma (Ollama local now, remote endpoint later) are called **only by
the backend**, only through the sealed real adapters, only when deterministic checks decide
a model call is warranted.

---

## 3. Client vs backend responsibility split

| Concern | Where | Why |
|---|---|---|
| All reasoning, evidence assembly, thresholds | backend | cost, secrecy, determinism |
| Today state computation + versioning | backend | single source of truth |
| Rendering templates (five-state wording) | backend (stable templates) + client presents verbatim | semantic stability is server-guaranteed |
| Conversation semantics (follow-ups) | backend QnA service | rollover/budget rules are contract items |
| Local cache of last payloads, offline display | client | UX only; never authoritative |
| Charts for Evidence Level 3 | client renders series fetched from backend | raw series never shipped to model, only to UI |
| Auth token storage | client secure storage | |
| Notification display | client (local) | MVP; server decides *what/when* |

---

## 4. Data access strategy

### 4.1 Direct L6 reads (no projection needed)

- Current `daily_reasoning` + `hypotheses` + `medical_reviews` → Today base content.
- `personal_context` (CURRENT) → Context list/edit surface.
- `personal_patterns` → 我的规律 (filtered).
- `qa_sessions` → history of asked questions (audit), not the chat transcript store.

### 4.2 Read-model projections (L7-owned tables)

| Projection | Source | Purpose |
|---|---|---|
| `today_versions` | L6 daily_reasoning/evidence_bundles + rendered copy | semantic stability, "更新于 HH:MM", "判断已更新", version history |
| `health_episodes` + `episode_events` | L5 persistence/change-point/trend + L6 reasoning versions + context/feedback | History first-class organization (start/develop/change/recover) |
| `notification_decisions` | Today deltas + settings | restraint audit trail (every suppressed push is recorded with reason) |
| `conversations` + `qa_turns` | QnA service | short-term conversation semantics + rollover |
| `context_time_meta` | L6 personal_context ids | occurrence/duration/end/expiry/last-confirm semantics (§28) without touching L6 schema |
| `settings` | user | notification mode, quiet hours, data prefs |
| `evidence_change_log` | bundle hashes, upstream checkpoints | "这次更新发生了什么" |

### 4.3 Caches

- **Model-call cache**: keyed by `request_sha256` (canonical bundle + operation). Identical
  input → reuse stored output (L6 `model_invocations` already hashes requests; L7 mirrors
  the key before paying for a call). This is the "相同 input 尽量复用结果" rule.
- **Today payload cache**: in-memory + `today_versions` row; client GET is a pure read.
- **Evidence detail cache**: L3 series points for a given (feature, window) cached per
  `max(l3.updated_at_utc)` signature.

---

## 5. Today Service — dynamic live state

### 5.1 State model

Product state (five classes, contract §11) derived **deterministically** from L6 output:

```text
A 整体稳定          ← overall_state=STABLE, no medical trigger
B 有变化无需处理     ← overall_state=MILD_CHANGE, no medical trigger
C 今天值得调整       ← overall_state=NOTABLE_CHANGE, no medical trigger
D 无法可靠判断       ← overall_state=INSUFFICIENT_EVIDENCE (or confidence VERY_LOW with no primary)
E 健康安全关注       ← medical path: symptom context present, ACUTE_ILLNESS_SUSPECTED,
                       medical_review_state ∈ {REQUIRED, PERFORMED, UNAVAILABLE-with-trigger},
                       or ESCALATE findings
```

E takes precedence; then D; then A/B/C. This mapping is pure L7 presentation logic — it
never alters the underlying judgment. Information order (conclusion→cause→action vs
conclusion→action→cause for E) is enforced by the payload schema, not the client.

### 5.2 Hybrid triggers → EngineOrchestrator

```text
data readiness      : scheduler tick sees new L2 capture/checkpoint movement → run upstream
                      incremental chain (L2import→L3→L4→L5) → evaluate Today
app open            : GET /today → evaluate (cheap path first; model only if warranted)
new sensor data     : same as data readiness (checkpoint movement is the detector)
user adds context   : ContextService → L6 ingest → evaluate
user feedback       : FeedbackService → L6 feedback → evaluate
```

### 5.3 Three thresholds (separate, ordered)

```text
Recompute Threshold  : bundle input changed?  (upstream checkpoints moved, or new CURRENT
                       context/feedback rows, or analysis date advanced)
                       → yes: re-assemble bundle. If bundle_sha256 differs from the last
                       materialized hash (or context/feedback changed), run L6 reasoning
                       (DeepSeek) once; else skip the model entirely.
UI Change Threshold  : presented-judgment signature changed?
                       sig = (product_state, primary_hyp, secondary_hyp, confidence,
                              canonical(sorted actions), medical_state)
                       → unchanged: keep the exact rendered wording, bump only "更新于".
                       → changed: insert new today_version, mark "判断已更新".
Notification Threshold: strictly stricter than UI change:
                       notify only if (a) medical-safety event (E onset or ESCALATE), or
                       (b) state moved into/out of C/E in a way that changes today's
                       actionable advice, and mode allows it. "If the user did not know
                       this now, would it change their next reasonable decision?"
```

All three decisions are logged (`notification_decisions`, `evidence_change_log`) so
restraint is auditable.

### 5.4 Semantic stability

- Wording is produced by a **deterministic renderer**: `(product_state, primary_hyp,
  confidence, medical_state)` → fixed template text (Chinese), with model-provided
  `reasoning_summary`/actions shown verbatim as cause/actions. No re-paraphrase per refresh.
- When model output updates but the signature is unchanged, the previous rendered copy is
  kept (only `updated_at` moves). Synonym churn is structurally impossible.
- Model actions are capped at 3 (highest-value first); stable state renders 0 actions.

### 5.5 Today versioning & provenance

- `today_versions`: append-only. Fields: version id, timestamp, trigger,
  `l6_daily_reasoning_id`, `bundle_sha256`, product_state, signature, rendered copy,
  change summary ("这次更新发生了什么": new evidence items / new context / feedback).
- Old versions never deleted/overwritten (contract §17, §39).
- Provenance shown to user comes from L6 `reasoning_provenance` + bundle items (Evidence
  Level 2 = plain-language facts from the bundle; Level 3 = L3/L4/L5 drill-down values).

---

## 6. Q&A Service

- Endpoint `POST /qa/ask` within a conversation. Server assembles the **Personal Evidence
  Bundle**: current Today state + relevant L5 deviations + L4 baselines + recent relevant
  context + relevant patterns + recent feedback (deterministic relevance selection by the
  question's metric/topic keywords; full bundle when unclear). Chat history contributes
  *conversation semantics only*; health facts always from engine.
- Answer flow: `assemble_evidence` → candidates → `medical_trigger(question, …)` →
  if REQUIRED: MedGemma review (local Ollama now; remote later) → DeepSeek
  `answer_question` (Flash, thinking disabled, temperature 0, JSON) → validated → answer-first payload:
  `direct_answer, reason, actions[], evidence_ref`. Insufficient evidence → explicit
  "目前不能可靠判断 + 缺什么" payload shape.
- **Conversation lifecycle**: conversations table with `boundary` logic — a conversation
  closes when (system date advanced) AND (a long sleep episode ended, i.e. latest sleep
  episode for the new day exists OR no sleep expected yet + first open of the new day).
  New question after boundary → new conversation; prior short-term context excluded;
  durable facts (context/feedback/patterns/evidence) remain retrievable.
- Scope guard: questions answered only within the health-decision domain; out-of-scope
  questions get a fixed refusal payload (no ChatGPT drift), deterministic classifier
  (keyword + model fallback).

---

## 7. Context Service

- Ingest from anywhere (Q&A text, Context page, quick chips 熬夜/高强度训练/喝酒/身体不舒服/
  压力大/其他): text → `RealDeepSeek.extract_context` (fallback: sealed deterministic
  keyword extractor) → `l6_context_ingest` write path (USER_REPORTED, raw text retained).
- Auto-save, non-blocking, light display chip, always editable. No confirmation dialogs
  except when extraction confidence is low AND the fact would materially change the current
  judgment (contract §25) — then one compact confirm.
- Ambiguity tolerated: fields left unknown; follow-up question only when information value
  is high. Question budget: at most **one** pending system question at a time, tracked in
  `context_time_meta.question_budget`; the system suppresses questions whose answers it can
  already infer from evidence (learning = counters per question type).
- Time semantics (§28): `context_time_meta` stores occurred_at, ongoing flag, ended_at,
  valid_until, last_confirmed_at. A scheduler tick expires stale active contexts (e.g.
  one-shot events auto-end after their natural window; ongoing symptoms require
  re-confirmation after N days or are marked ENDED/UNCERTAIN — surfaced as a lightweight
  question, never a questionnaire).
- Corrections: user edit → L6 CORRECTION revision (old row SUPERSEDED) → immediate
  Today re-evaluation. Fact priority USER_CORRECTION > AI_STRUCTURED > AI_INFERRED is
  inherent: corrections write USER_REPORTED rows that supersede; AI extraction never
  touches non-USER_REPORTED classes.

## 8. Feedback Service

- Surfaces only at important nodes (new notable judgment, meaningful change, uncertain
  judgment, new ESTABLISHED pattern): payload flags `feedback_prompt = true`.
- Input: 准确 / 不太准确 / 补充情况 (free text). Server AI-structures into
  `judgment_confirmed | judgment_rejected | reason_corrected | context_added |
  action_helpful | action_not_helpful` (deterministic mapping + model fallback) →
  `l6_feedback` write path → if fact base changed: rebuild evidence → re-run L6 (and
  medical review if triggered) → Today/QnA update. `notification_decisions`-style audit
  row records the chain (feedback_id → re-evaluation run → new today_version).

## 9. History Service (episodes)

- Episode builder (nightly + on-demand projection): groups consecutive related L5 changes
  (persistence/change-point/trend on related metrics) with overlapping L6 judgment versions
  into episodes with phases start → develop → change → recover/end. Stable days stay
  hidden (but rows exist) — timeline shows meaningful events only.
- Episode record keeps: evidence-at-time (bundle hash + ids), judgment-at-time (reasoning
  ids), subsequent context/feedback, corrections, outcome — never erased (§39).
- Natural-language historical search: query → structured search over episodes
  (metric/type/date span/similarity via bundle signature overlap) → grounded answer;
  MVP uses deterministic matching + optional model phrasing.

## 10. Pattern Projection (我的规律)

- Read `personal_patterns`; show only actionable ones: maturity ESTABLISHED, or OBSERVING
  with support≥2 and a decidable action. Each card: trigger → outcome in plain language,
  "过去 N 次中 M 次出现", time span, counter-examples (total − support), maturity state;
  accumulation status shown when nothing qualifies yet ("正在积累证据: 已观察 n 次").
- Lifecycle upgrade/downgrade/revise/invalidate comes from the L6 counters + L7 projection
  status field (a pattern whose support ratio collapses is marked revised/downgraded in the
  projection, source counters untouched).

## 11. Notification Service

- Modes: `QUIET` (safety only), `SMART` (default; decision-relevant major changes +
  safety), `DAILY` (daily state update + major changes). Quiet hours respected.
- Gate = Notification Threshold (§5.3) applied per mode; every decision (sent or
  suppressed) logged with reason → testable restraint.
- Transport: MVP local/in-app feed; interface `NotificationTransport` isolates future
  push providers (no client/server logic change later).

## 12. Auth boundary

- MVP single-user but **multi-user-ready**: every L7 table carries `user_id`
  (default `owner`); API routes resolve the authenticated user; no global singleton state.
- Auth: bearer token issued by backend (`POST /auth/token` with pre-shared setup secret in
  dev config; future: OAuth/passkey). Tokens hashed at rest; no health data in logs
  (structured logs carry ids/hashes only).
- Secrets: env vars only (`.env` local, never committed; `.env.example` committed).

## 13. Scheduler & incremental refresh

- Async tick loop (single process):
  1. every N minutes: check L1 capture dir / L2 `captures` max timestamp vs stored
     watermark → if new: run upstream incremental chain (subprocess, sealed CLIs) in order
     L2-import → L3 → L4 → L5 (each is checkpoint-based, idempotent);
  2. after chain or on trigger: TodayService.evaluate() (threshold logic §5.3);
  3. hourly: context time-semantics expiry sweep; conversation-rollover check;
  4. daily: DAILY-mode summary notification decision; episode projection rebuild.
- All model-triggering work is queued (one reasoning run at a time; lock row in L7 db).
- Dev machine runs this for now; the same loop runs in the cloud deployable — no
  Windows-specific calls in backend code (paths via config; subprocess CLIs invoked via
  configured interpreter paths).

## 14. Cloud migration strategy (Cross-layer Production Infrastructure)

- Backend is a single stateless-ish service + SQLite data volume: package as a container
  (Dockerfile + compose produced in Phase H; this machine has no Docker, so verification =
  build config + import smoke test, runtime proof on any Docker host).
- DeepSeek is already cloud API. MedGemma: `MEDICAL_MODEL_MODE=remote` +
  `MEDICAL_MODEL_ENDPOINT` point at a cloud Ollama-compatible host — sealed adapter
  already supports it.
- Upstream sync in the cloud: Xiaomi collection currently depends on the local collector
  (L1, SEALED). Migration path (documented, not implemented in MVP): run the same sealed
  collector inside the backend container/host with credentials in env; L2 import unchanged.
  Until then, the dev PC is a *data source* only — the **product** (app↔backend↔models)
  does not require it to be on: the backend serves the last-synced state and degrades
  gracefully ("数据截至 …").
- Backup: nightly `VACUUM INTO` snapshot of L7 db + existing L2 archive discipline;
  restore documented. Export/delete path: per-user repository interface makes
  export-all / delete-all per user_id a single function (extensible, §55).

## 15. Cost discipline (contract §53) — concrete mechanisms

1. Raw time series never leave the dbs for a model; only the compact Evidence Bundle.
2. Recompute Threshold gates every DeepSeek call (bundle hash + context/feedback delta).
3. Request-hash model-call cache reuses identical inputs.
4. Semantic stability prevents churn-driven re-rendering (no model involved in rendering).
5. MedGemma only on the sealed medical-trigger policy paths.
6. Every real request uses DeepSeek V4 Flash with thinking explicitly disabled.
7. temperature 0 everywhere → reproducible outputs → cache hits are real.
8. Every DeepSeek call emits a sanitized operation/model/thinking/usage audit event;
   model prompts, responses, health data, and credentials are excluded.

## 16. Repository layout (L7-owned code)

```text
D:\PersonalHealthEngine-L7\
  L7_ENVIRONMENT_DISCOVERY.md, L7_TECHNICAL_ARCHITECTURE.md, IMPLEMENTATION_PHASES.md
  backend\
    pyproject.toml / requirements.txt
    l7\
      config.py            (paths, env, modes; local/dev/prod separation)
      store\               (L7 db migrations + repositories; user_id everywhere)
      upstream\            (read-only access: l3/l4/l5/l6 readers; l6_bridge import seam)
      engine\              (EngineOrchestrator: thresholds, change detection, model-call cache)
      services\            (today, qna, context, feedback, history, patterns, notifications)
      rendering\           (five-state deterministic renderer, i18n zh-CN templates)
      api\                 (FastAPI app + routers + auth)
      scheduler\           (tick loop jobs)
    tests\                 (unit + integration; mock adapters default)
  app\                     (Flutter project: lib\features\today|history|patterns|me|qa|...)
  docs\                    (per-phase audits)
  .env.example, .gitignore (secrets never committed)
```

## 17. Explicit non-goals for MVP

- No health/readiness score, traffic-light, grade or star rating anywhere (contract §13).
- No generic dashboard / data explorer (§47): only evidence drill-down charts.
- No push-provider integration, no multi-tenant management, no community features.
- No Apple Health / Garmin / Fitbit / Whoop / Oura adapters (interface reserved via
  `upstream` package boundary only).
- No re-implementation or modification of any SEALED layer.
