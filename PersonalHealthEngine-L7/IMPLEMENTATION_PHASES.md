# L7 Implementation Phases

Date: 2026-08-17
Rule per phase: read existing code → define goal → write tests → implement → run tests →
phase audit → only then continue.

Status legend: ☐ pending · ▶ active · ☑ done

---

## Phase A — Environment Discovery ☑ DONE

- Input: `D:\PersonalHealthEngine-*`, SEAL/HANDOFF/CONTRACT/AUDIT docs, all dbs.
- Output: `L7_ENVIRONMENT_DISCOVERY.md`.
- Gate: upstream inventory, read boundaries, SEALED boundaries recorded; no guesswork. ✔

## Phase B — Technical Architecture ☑ DONE

- Input: Phase A results + Product Contract.
- Output: `L7_TECHNICAL_ARCHITECTURE.md` (stack: Python/FastAPI/SQLite backend, Flutter
  client; services, projections, thresholds, versioning, cost, cloud path).
- Gate: every contract area mapped to a component; no upstream modification planned. ✔

## Phase C — Backend contracts / Product API skeleton ☑ DONE

- Input: architecture doc; sealed L6 modules (import-only).
- Code scope:
  - `backend/l7/config.py`, `store/` (L7 db v1 migrations: today_versions, conversations,
    qa_turns, context_time_meta, notification_decisions, settings, episodes, evidence_change_log;
    user_id on all tables), `upstream/` read-only readers (L6 current judgment, bundles,
    context, patterns; L3/L4/L5 drill-down), `upstream/l6_bridge.py` (sys.path seam),
    `engine/change_detection.py` (Recompute Threshold: checkpoint + context/feedback delta;
    bundle hash compare), `rendering/` (five-state deterministic renderer + signature),
    `api/` (FastAPI app, auth bearer, routers: /today, /today/versions, /evidence/*,
    /patterns, /settings; contract DTOs), `engine/model_call_cache.py`.
  - EngineOrchestrator.evaluate() with mock adapters as default; real DeepSeek path
    implemented and gated (used when configured + recompute warranted).
- Tests (unit): state mapping (incl. E precedence), signature/semantic stability,
  change detection (no-change → zero model calls; changed bundle → one call via mock
  adapter counter), renderer caps (≤3 actions, 0 on stable), L7 db migration integrity.
- Acceptance gate: `GET /today` returns Current Today State built from the real L6 db
  (analysis_date 2026-08-16 row) with evidence refs; repeated calls do not invoke any
  model; audit `PHASE_C_AUDIT.json` PASS.

## Phase D — Today vertical slice (end-to-end) ☑ DONE

- Input: Phase C backend; Flutter toolchain.
- Code scope: Flutter app shell (4 bottom tabs 今日/历史/我的规律/我的, Q&A + Context entry
  on 今日), Today screen (状态/主原因/今日行动/更新于 HH:MM/查看依据 Level 2), evidence
  detail page (Level 3 charts: metric series from /evidence), Context input entry, Q&A
  entry; local cache of last /today payload; backend: today_versions history endpoint
  wired, "判断已更新" flag, "这次更新发生了什么" payload.
- Tests: widget tests (stable-day render, no-score UI assertions); integration: app → API
  → real L6 db; E2E stable day.
- Acceptance gate: on the device/emulator the Today screen shows 当前状态+最可能原因+今日行动
  +更新时间+查看依据+Context入口+Q&A入口 from live L1–L6 data; `PHASE_D_AUDIT.json` PASS. ✔
- Note: APK build initially blocked by CN network (dl.google.com unreachable); fixed via
  Aliyun mirrors in app gradle files + patched Flutter SDK includeBuild settings.

## Phase E — Q&A + Context + Feedback ☑ DONE

- Input: Phase D.
- Code scope: QnAService (Personal Evidence Bundle assembly, answer-first DTO, medical
  trigger routing to MedGemma via sealed adapter, conversation table + rollover rule,
  scope guard); ContextService (DeepSeek extract_context with deterministic fallback,
  l6_context_ingest write path, auto-save + edit + correction → SUPERSEDED revision,
  context_time_meta expiry sweep, one-question budget); FeedbackService (AI structuring,
  l6_feedback write path, re-evaluation chain feedback→evidence→L6→medical→Today).
- Tests: unit (context parsing integration, rollover, feedback trigger classification,
  question budget); integration (Context→re-evaluation updates Today; Feedback CORRECTED
  supersedes fact and re-renders Today; Medical Critic routing fires only on trigger
  policy; no model call on irrelevant chatter); E2E cases: Midday Context update,
  Correction, Overnight rollover, Insufficient Evidence answer.
- Acceptance gate: `PHASE_E_AUDIT.json` PASS with those E2E cases green. ✔
  (backend 66/66, widget 5/5, dart E2E 14/14; 4 contract E2E cases PASS)

## Phase F — History + Personal Patterns ☑ DONE

- Input: Phase E.
- Code scope: HistoryService episode projection (grouping rules, phases, stable-day
  hiding, versioned evidence/judgment/context/feedback per episode, NL historical search
  over episodes); PatternProjection (actionability filter, counterevidence rendering,
  accumulation state, upgrade/downgrade/revise/invalidate projection status); Flutter
  History + 我的规律 screens.
- Tests: unit (episode grouping, pattern presentation, no-single-event-pattern rule,
  counterevidence math); integration (episode ← real L5/L6 history); E2E History browsing.
- Acceptance gate: `PHASE_F_AUDIT.json` PASS. ✔ (backend 76/76; episode projection + pattern display_status green)

## Phase G — Notifications + Settings ☑ DONE

- Input: Phase F.
- Code scope: NotificationService (QUIET/SMART/DAILY modes, Notification Threshold gate,
  quiet hours, decision audit log, in-app feed + local notification transport); Settings
  screen + endpoints (mode, quiet hours, diagnostics: model-call/token counters).
- Tests: unit (threshold vs UI-change separation; stable day → no push; safety → push in
  all modes; SMART vs DAILY deltas); E2E Notification restraint case.
- Acceptance gate: `PHASE_G_AUDIT.json` PASS. ✔ (backend 84/84; threshold separation + restraint E2E green)

## Phase H — Cloud-ready packaging ☑ DONE

- Input: Phase G.
- Code scope: Dockerfile + docker-compose (backend + volume), deployment doc (VPS path,
  MedGemma remote endpoint option, Xiaomi collector migration note), backup script
  (`VACUUM INTO` nightly + retention), per-user export/delete function, env split
  local/dev/prod, secret handling review.
- Tests: packaging smoke (app imports & starts headless with prod-like env), backup/restore
  round-trip test.
- Acceptance gate: `PHASE_H_AUDIT.json` PASS; documented proof that app↔backend↔models
  does not require the dev PC to be powered on (backend deployable independently; data
  freshness degrades gracefully with visible "数据截至" stamp). ✔ (backend 89/89;
  packaging smoke + backup round-trip green; final `docker compose up` happens on the VPS
  because Docker is not installed on the dev PC)

## Phase I — Full acceptance & seal ☑ DONE

- Input: all phases.
- Code scope: full test suite run (unit + integration + all 10 contract E2E scenarios):
  Stable day · Minor change · Actionable change · Insufficient Evidence · Medical safety ·
  Midday Context update · Correction · Overnight rollover · Semantic stability ·
  Notification restraint.
- Output: `L7_FINAL_AUDIT.json`, `L7_SEAL.md`, `L7_HANDOFF.md`.
- Acceptance gate: every acceptance criterion of contract §59 PASS, including
  "评分系统不存在" static audit and "开发 PC 不属于最终运行依赖". Only then `L7 = SEALED`.
- Result: backend 89/89 · flutter analyze 0 errors · widget 5/5 · dart E2E 18/18 ·
  upstream integrity PASS · all 10 scenarios PASS → **L7 = SEALED**. ✔

---

## Cross-phase standing rules

1. Mock adapters are the default in all automated tests (deterministic, free). Real
   DeepSeek/MedGemma calls happen only behind explicit gates, with a per-phase call budget,
   and every real call is recorded (tokens, latency) in the phase audit.
2. No mock result may enter the formal path disguised as real reasoning (contract §61):
   mock usage is always labeled `mock-*` in `reasoning_model`, as the sealed L6 does.
3. Every phase writes `docs/PHASE_X_AUDIT.json` with tests run, results, model-call counts.
4. Upstream dbs are opened read-only (`mode=ro`) by L7 code; write paths go through sealed
   L6 entry points only; no FULL_REBUILD on production data.
5. Secrets stay in environment variables; `.env` never committed; logs carry ids/hashes,
   not health content.
