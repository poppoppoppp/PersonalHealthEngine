# PHE Performance & Responsiveness V1 Design

Date: 2026-08-24
Status: Approved by the supplied production brief
Baseline: `ff5ddd948922da358034917046a734176df03896`

## Measured diagnosis

Idle production reads are not the main server-side bottleneck: external p95 was 113 ms for
Today, 85 ms for History, 99 ms for Patterns, and 166 ms for Context. The user-visible read
problem is instead caused by an app that caches only Today and renders loading-only states for
every other screen. The backend also performs unnecessary work on reads: `/today` invokes the
orchestrator and `/history/episodes` rebuilds its projection on every request.

The dominant system failure is CPU starvation during medical review. A real production decision
request took 500.4 seconds and, while it ran, `/health` did not respond within 20 seconds despite
an idle p95 of 138 ms. Context and Feedback are also synchronous: each waits for DeepSeek and a
Today re-evaluation before acknowledging the durable user write. Product-meta and deterministic
health-data Q&A both take 1.2-1.3 seconds because every question first calls DeepSeek semantic
classification.

## Approaches considered

1. Add only Flutter caches and shorten the MedGemma timeout. This improves some perceived reads
   but leaves the host unavailable during inference and does not fix synchronous writes.
2. Add a focused SQLite-backed performance layer: fast read projections, durable jobs, exact
   medical-review cache, compact reviewer input/output, one resource-limited worker, conditional
   requests, pagination, and versioned Flutter stale-while-revalidate caches.
3. Introduce Redis/Celery plus a Daily Medical Safety Envelope. This can scale further, but adds
   infrastructure and changes the sealed medical orchestration contract before simpler measured
   optimizations have been exhausted.

Approach 2 is selected. It is the smallest architecture that fixes both perceived latency and the
measured resource-starvation failure. Approach 3's safety envelope remains a gated fallback only
if the optimized real MedGemma path is still unsuitable.

## Backend architecture

Reads return L7-owned projections only. `GET /today` returns the latest `today_versions` payload
without invoking L6. History list reads `health_episodes`; projection rebuilding happens after
pipeline/Today changes. Patterns are materialized into a versioned L7 snapshot after the pipeline
or relevant feedback. Growing collections use bounded cursor pagination with stable descending
IDs and a default first page of 20-50 items.

Context and Feedback first complete their authoritative SQLite write in a short transaction, then
enqueue a deduplicated durable job and return `202 Accepted` with a job ID. A single worker performs
context extraction when required, Today re-evaluation, History/Patterns refresh, and other model
work. Job status is polled by the private client. Health conclusions are never updated optimistically;
only the persisted submission receives immediate acknowledgement.

Request middleware records sanitized stage durations and response sizes using request IDs. The L7
database stores aggregateable request/model/job measurements, not prompt or health text. A private
CLI emits p50/p95/p99, error rates, queue wait, cache hits, and slow-query summaries.

## Q&A and medical review

Fixed product-meta questions receive a deterministic local fast path before semantic classification.
Deterministic health-data questions keep the existing engine-authoritative path; a conservative local
intent recognizer handles only unambiguous registered metric queries and falls back to DeepSeek for
everything else. Health decisions keep the existing fail-closed ordering.

MedGemma receives a versioned `MedicalReviewBundle` containing only the question, validated candidate,
candidate actions/claims/evidence refs, resolved evidence behind those refs, current medical state,
relevant symptoms/context, safety facts, and uncertainty. The critic returns a minimal fixed schema.
The benchmark matrix selects the smallest `num_predict`, `num_ctx`, `num_thread`, `num_batch`,
keep-alive, quantization, and Ollama version that preserve schema validity and the existing medical
regression set.

An exact deterministic review cache is keyed by normalized question/classification, complete candidate,
review bundle hash, medical state, model artifact hash, prompt version, and schema version. Any changed
safety input is a miss. Concurrent identical reviews coalesce to one job. Required review remains
fail-closed on errors and never exposes an unreviewed candidate.

Ollama remains private and single-concurrency. It runs with a lower CPU scheduling priority and a
measured thread count so FastAPI/Nginx retain CPU time. The model is kept warm only if the 8 GiB host
passes memory and reboot tests. Google publishes no official text-only 4B MedGemma; the official
text-only variant is 27B, so no custom 4B derivative is adopted without provenance and full regression.

## HTTP and Flutter data flow

Versioned read responses include `version_id`/`updated_at`, ETag, and cache-control suitable for a
private stale-while-revalidate client. `If-None-Match` returns 304 when unchanged. Payloads contain
only the requested first page.

Flutter opens with the last known-good Today and shell immediately. A shared repository owns small
versioned caches for Today, History page 1, Patterns, recent Context, and Timeline page 1. Screens render
cached data synchronously, then refresh without clearing it. One in-flight request per cache key is shared
across widgets; app start performs bounded prefetch of the first pages only. Newer versions cannot be
overwritten by stale responses. Lists use builder-based lazy rendering and request the next cursor near
the end. Corrupt cache entries are discarded independently.

## Consistency and failure behavior

Every cache has a schema version, server fingerprint, authoritative version/ETag, and explicit invalidation
events. Pipeline completion, Context/Feedback completion, and Today version changes refresh dependent
projections. Old last-known-good content remains visible during network/model failure. Jobs use idempotency
keys, bounded retries with backoff, and single-worker claiming so a client retry cannot duplicate model work.

SQLite remains the authoritative single-user store with WAL and short transactions. Each audited query is
checked with `EXPLAIN QUERY PLAN`; indexes are added only where the plan demonstrates need. No L3-L6 health
semantics change.

## Verification and rollout

All behavior changes use red-green tests. Required regression coverage includes bounded pagination, no
read-time projection rebuild, durable write-before-ack, async jobs, exact medical cache invalidation,
in-flight deduplication, stale-version rejection, cache corruption, and normal API latency during a real
MedGemma request. Medical tuning uses the existing safety cases plus schema validity checks before any
production switch.

Deployment is rolling and reversible: migrate L7, deploy the worker and tuned Ollama unit, rebuild the API,
run authenticated production smoke/latency/load tests, reboot the VPS, verify protected ports and timers,
then build and verify the signed Android APK. `PHE_PERFORMANCE_BASELINE.json` and
`PHE_PERFORMANCE_AUDIT.json` record before/after evidence without sensitive content.
