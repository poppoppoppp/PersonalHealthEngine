# PHE Performance & Responsiveness V1 Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Make PHE render trustworthy cached content immediately, acknowledge durable writes quickly, and keep normal production APIs responsive during real MedGemma inference without changing health facts or medical safety.

**Architecture:** L7 serves bounded versioned projections and owns a SQLite durable job queue. A single background worker performs model/recompute work, while exact medical-review caching and a compact critic bundle reduce repeated and intrinsic MedGemma cost. Flutter uses one versioned stale-while-revalidate repository with in-flight request coalescing and cursor pagination.

**Tech Stack:** Python 3.14, FastAPI, SQLite WAL, pytest, Flutter/Dart, SharedPreferences small-payload cache, DeepSeek V4 Flash, Ollama/MedGemma 1.5, systemd, Docker Compose, Nginx.

---

### Task 1: Sanitized performance instrumentation and baseline harness

**Status:** Complete (2026-08-24)

**Files:**
- Create: `PersonalHealthEngine-L7/backend/l7/performance.py`
- Create: `PersonalHealthEngine-L7/backend/scripts/performance_report.py`
- Create: `PersonalHealthEngine-L7/backend/scripts/benchmark_production.py`
- Create: `PersonalHealthEngine-L7/backend/tests/test_performance.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/store/db.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/api/app.py`
- Update: `PHE_PERFORMANCE_BASELINE.json`

**Step 1: Write failing tests** for request IDs, endpoint/total/db/bundle/model/finalizer/serialization timings, response byte counts, aggregate p50/p95/p99, and the invariant that prompt/body/token/secret values never enter telemetry.

**Step 2: Verify RED.** Run `python -m pytest PersonalHealthEngine-L7/backend/tests/test_performance.py -q`; failures must identify the missing schema/middleware/report.

**Step 3: Implement the minimum instrumentation.** Add migration 3 tables for sanitized request, stage, model, job, cache, and slow-query metrics. Use a request-local timer and response-size middleware. Extend the Ollama adapter to persist native `load_duration`, `prompt_eval_count/duration`, `eval_count/duration`, and `total_duration` supplied by Ollama.

**Step 4: Verify GREEN.** Run the focused test, then `python -m pytest PersonalHealthEngine-L7/backend/tests -q`.

**Step 5: Review and commit.** Review privacy fields and diff scope; commit `feat: add sanitized performance telemetry`.

### Task 2: Fast bounded read models, pagination, and conditional HTTP

**Status:** Complete (2026-08-24)

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/store/db.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/today.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/history.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/context.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/api/app.py`
- Create: `PersonalHealthEngine-L7/backend/tests/test_performance_reads.py`

**Step 1: Write failing tests** proving `GET /today` never calls `orchestrator.evaluate`, History list never calls `rebuild`, growing collections cap the first page, cursor order has no duplicates/gaps, and matching ETags return 304.

**Step 2: Verify RED** with `python -m pytest PersonalHealthEngine-L7/backend/tests/test_performance_reads.py -q`.

**Step 3: Implement bounded reads.** Return the last `today_versions.rendered_json`; materialize History after update events; add stable ID cursors (default 30, max 50) for episodes/timeline/context/feedback/Q&A; add explicit projection versions and ETags. Keep empty-state compatibility and authoritative source references.

**Step 4: Audit SQLite.** Run `EXPLAIN QUERY PLAN` fixtures for each list query. Add only demonstrated composite indexes. Tests must fail on `SCAN` of growing tables or temp B-tree sorts where an index should satisfy the query.

**Step 5: Verify GREEN and regressions.** Run focused tests plus `test_api_today.py`, `test_history.py`, `test_context_feedback.py`, and `test_qna.py`.

**Step 6: Review and commit.** Commit `perf: serve bounded versioned read projections`.

### Task 3: Durable asynchronous Context and Feedback pipeline

**Status:** Complete (2026-08-24)

**Files:**
- Create: `PersonalHealthEngine-L7/backend/l7/jobs.py`
- Create: `PersonalHealthEngine-L7/backend/l7/worker.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/store/db.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/context.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/feedback.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/api/app.py`
- Create: `PersonalHealthEngine-L7/backend/tests/test_jobs.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/test_context_feedback.py`

**Step 1: Write failing tests** for write-before-ack, `202` job responses, idempotency/dedup, one-worker atomic claiming, stale claim recovery, bounded backoff, and Context/Feedback handlers returning without running DeepSeek or Today evaluation.

**Step 2: Verify RED.** Run the two focused test files.

**Step 3: Implement migration and repository.** Jobs carry kind, user, sanitized payload reference, idempotency key, status, attempts, timestamps, result version, and error category. Persist unstructured input in a private L7 submission row until the worker turns it into an authoritative L6 fact; job and performance telemetry store no raw text.

**Step 4: Implement worker.** One process claims one job using a short `BEGIN IMMEDIATE`, releases the transaction, performs extraction/recompute, writes result, and refreshes dependent projections. Duplicate submissions return the existing job.

**Step 5: Add job status endpoint** and preserve compatibility fields while adding `accepted`, `job_id`, `status`, and `persisted_at`.

**Step 6: Verify GREEN and concurrency.** Run focused tests with simultaneous clients and the full L7 backend suite.

**Step 7: Review and commit.** Commit `feat: move health recompute to durable jobs`.

### Task 4: Q&A fast paths, compact MedicalReviewBundle, and exact review cache

**Status:** Complete (2026-08-24)

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/qna_orchestration.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py`
- Modify: `PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/store/db.py`
- Create: `PersonalHealthEngine-L7/backend/tests/test_medical_review_performance.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/test_qna.py`
- Modify: `PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py`

**Step 1: Write failing tests** proving fixed product-meta bypasses every model, unambiguous registered health-data queries bypass semantic DeepSeek, ambiguous questions fall back safely, compact review bundles omit unrelated history, and candidate-before-review/fail-closed behavior is unchanged.

**Step 2: Write exact-cache tests** covering every key input: classification/question representation, full candidate, resolved evidence hash, medical state, model artifact hash, critic prompt version, and schema version. Change each input independently and require a miss. Concurrent identical reviews must coalesce.

**Step 3: Verify RED.** Run focused L7/L6 tests.

**Step 4: Implement conservative fast routing.** Match only fixed product copy and canonical metric/time/aggregation grammar. Do not grant general scope authority to keywords.

**Step 5: Implement `MedicalReviewBundle/v1`.** Resolve only candidate-referenced evidence plus relevant symptoms/current medical state/safety facts. Use a short critic JSON schema and configurable `num_predict`, `num_ctx`, `num_thread`, `num_batch`, and `keep_alive`. Capture native Ollama timings.

**Step 6: Implement exact deterministic cache and in-flight coalescing.** Cache only schema-valid reviewed results and fail closed on corrupt entries.

**Step 7: Verify medical safety.** Run `test_qna.py`, `test_product_conformance.py`, all L6 tests, and a prompt-size/token-count regression fixture.

**Step 8: Review and commit.** Commit `perf: compact and cache medical review`.

### Task 5: Flutter stale-while-revalidate repository and immediate navigation

**Status:** Complete

**Files:**
- Create: `PersonalHealthEngine-L7/app/lib/read_cache.dart`
- Create: `PersonalHealthEngine-L7/app/lib/data_repository.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/main.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/api_client.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/today_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/history_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/patterns_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/context_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/qa_screen.dart`
- Create: `PersonalHealthEngine-L7/app/test/read_cache_test.dart`
- Create: `PersonalHealthEngine-L7/app/test/data_repository_test.dart`
- Modify: `PersonalHealthEngine-L7/app/test/api_client_test.dart`
- Modify: `PersonalHealthEngine-L7/app/test/widget_test.dart`

**Step 1: Write failing cache tests** for schema/server/version metadata, corruption isolation, newer-version wins, explicit invalidation, ETag/304 handling, and one in-flight request per key.

**Step 2: Verify RED.** Run the new Dart test files.

**Step 3: Implement one small repository.** Reuse SharedPreferences only for bounded first-page JSON payloads. Initialize preferences once, render cached Today/shell immediately, then prefetch History page 1, Patterns, Context page 1, and Timeline page 1 without blocking paint.

**Step 4: Convert screens to SWR.** Never clear existing content on refresh; show a local updating indicator/error. Use `ListView.builder` and cursor loading. Preserve health-authority boundaries.

**Step 5: Implement async write UX.** Show persisted Context/Feedback acknowledgement and queued status immediately; poll the existing job with bounded backoff; refresh Today only when a new version is complete.

**Step 6: Verify request behavior.** Tests prove repeated widgets/tabs share one GET, retry does not duplicate a job, and stale responses do not overwrite newer cache.

**Step 7: Verify Flutter.** Run `flutter test` and `flutter analyze --no-fatal-infos`.

**Step 8: Review and commit.** Commit `perf: render cached app data immediately`.

### Task 6: Production model benchmark and resource isolation

**Status:** In progress

**Files:**
- Create: `deployment/scripts/benchmark_medgemma.py`
- Create: `deployment/scripts/benchmark_concurrency.py`
- Create: `deployment/systemd/phe-l7-worker.service`
- Modify: `deployment/systemd/ollama.service`
- Modify: `deployment/docker/docker-compose.production.yml`
- Modify: `deployment/scripts/install_server_config.sh`
- Modify: `deployment/tests/test_mobile_production.py`

**Step 1: Write failing deployment tests** for a single worker, restart policy, private ports, lower Ollama CPU priority, bounded threads/concurrency, explicit keep-alive, and no secret/prompt logging.

**Step 2: Verify RED.** Run `python -m pytest deployment/tests -q`.

**Step 3: Deploy instrumentation-only build** with rollback backup, migrate, and capture native BEFORE stage timings on the exact production prompt.

**Step 4: Benchmark one variable at a time.** Matrix: cold/warm/long/`-1` keep-alive; `num_predict` 64/96/128/192/256; `num_ctx` 1024/2048/4096; `num_thread` 1/2/auto; reasonable batch values; current Ollama, stable 0.24 candidate, latest stable; current Q4_K_M and only provenance-verified alternate quantizations. Record memory and schema/medical regression for every accepted point.

**Step 5: Select and encode only measured winners.** Keep the current model/quantization/version when alternatives do not provide a safe material gain. Do not adopt a custom text-only artifact without source hash and full regression.

**Step 6: Apply resource isolation.** Run Ollama/model work at lower scheduling priority with one inference at a time. Verify Nginx/FastAPI/SQLite retain CPU and memory headroom.

**Step 7: Run real concurrency acceptance.** While a real reviewed decision runs, concurrently request Today, Timeline, Patterns, and Context. p95 reads must remain within targets; Context durable ack must remain under 500 ms.

**Step 8: Review and commit.** Commit `ops: isolate and tune medical inference`.

### Task 7: Full acceptance, deployment, APK, and integration

**Files:**
- Create: `PHE_PERFORMANCE_AUDIT.json`
- Update: `docs/plans/task.md`
- Update: `PersonalHealthEngine-L7/L7_SEAL.md` and audit only if a contract version changes
- Output: `artifacts/PHE-Android-production.apk`

**Step 1: Run full local verification.** `python -m pytest -q`; `flutter test`; `flutter analyze --no-fatal-infos`; upstream integrity; DeepSeek model audit; secret scan; `git diff --check`.

**Step 2: Deploy pinned commit** to `/opt/phe` with database/code rollback backups, migrations, image rebuild, service reload, and health gates before removing the old container.

**Step 3: Run production benchmarks.** Capture idle and during-MedGemma p50/p95/p99, payload bytes, cache hits, queue wait, Q&A categories, exact review-cache latency, and Context acknowledgement. Write only sanitized results to `PHE_PERFORMANCE_AUDIT.json`.

**Step 4: Reboot VPS.** Verify Ollama, L7 API, worker, Nginx, HTTPS, daily timer, cert renewal, protected ports, Xiaomi acquisition, and L1-L7 daily pipeline recovery.

**Step 5: Build signed release.** Run `deployment/scripts/build_android_release.ps1`; verify V2 signature, signer, package, INTERNET, backup disabled, production URL, and SHA-256. Never create a new keystore.

**Step 6: Final review.** Compare every acceptance item and actual target; do not claim PASS for unmeasured fields. Calculate minimum hardware only if software optimization still cannot preserve acceptable interaction.

**Step 7: Integrate.** Merge `codex/phe-performance-v1` to main without reset, push main, verify remote HEAD and clean worktree.
