# Async Medical Q&A Design

## Decision

Keep deterministic PRODUCT_META, OUT_OF_SCOPE, and HEALTH_DATA questions synchronous. Persist every other Q&A request as a `QA_ASK` durable job and return HTTP 202 only after the SQLite transaction commits. The existing single worker performs DeepSeek reasoning and any required MedGemma review. The authenticated job-status endpoint returns the completed answer to the owning user; until then the Flutter client shows a safety-check state and never displays the unreviewed candidate.

## Alternatives considered

1. Keep the request synchronous. This preserves the old API but leaves users waiting 8–10 minutes and is rejected by the production benchmark.
2. Return the DeepSeek candidate before MedGemma finishes. This is faster but violates the fail-closed medical boundary and is rejected.
3. Persist and poll a single-consumer job. This is the selected minimal extension because the SQLite queue, worker, status endpoint, and polling UX already exist.

## Data flow

`POST /qa/ask` applies only the deterministic classifier. Fast scopes execute immediately. Other scopes enqueue the question and optional conversation ID with an idempotency key, then return 202. The worker calls the unchanged authoritative `QnAService.ask`, which stores the conversation and audit only after the normal finalizer. `GET /jobs/{id}` joins the owning submission and returns its result only after success.

## Failure and safety behavior

Jobs retain bounded retry/backoff and single-consumer execution. Failed jobs expose only a sanitized error category. Completed medical answers retain the existing review state and fail-closed behavior. No candidate, prompt, question, answer, secret, or personal context is written to performance logs.

## Contract

L7 reports version `0.2.0`. The old immediate JSON response remains for deterministic fast scopes. Deferred responses use the already established `{accepted, job_id, status}` shape and the existing authenticated job-status resource. The Flutter client supports both forms.

## Verification

Tests cover write-before-202, idempotent deduplication, worker completion, result ownership, no result before success, synchronous fast scopes, Flutter polling/success/failure, and the existing medical fail-closed regression suite.
