# DeepSeek V4 Flash Production Cutover Design

**Date:** 2026-08-24  
**Status:** Approved  
**Scope:** DeepSeek implementation and production cost configuration only

## Objective

Route every real PHE DeepSeek request through `deepseek-v4-flash` with thinking
explicitly disabled. Preserve all existing L1-L7 reasoning, medical-review, product,
and structured-output contracts.

## Current State

- The shared L6 real adapter defaults to `deepseek-v4-pro` and sends
  `reasoning_effort`.
- L7 Today, Q&A, product-copy translation, and Context all delegate to that shared
  transport.
- Feedback has no independent model path; correction text delegates to Context
  extraction.
- `/etc/phe/runtime.env`, the running `l7-backend` container, and the daily systemd
  unit currently resolve the Pro/high configuration.

## Selected Design

### Centralized, fail-closed transport

The shared real DeepSeek adapter is the enforcement boundary:

- Default model: `deepseek-v4-flash`.
- Every chat-completions payload includes `"thinking": {"type": "disabled"}`.
- Production request construction no longer accepts or sends `reasoning_effort`.
- A configured model other than `deepseek-v4-flash` is rejected before network I/O.

L7 call sites identify the operation (`today`, `qna`, `context`, or
`product_translation`) but do not control model or thinking settings. This prevents
per-surface drift while leaving prompts and reasoning semantics unchanged.

### Sanitized invocation audit

After a successful response, the adapter emits one structured log event containing:

- operation;
- requested model;
- response model identifier;
- thinking mode (`disabled`);
- token usage, when returned.

The event never includes the API key, prompts, user content, or model response body.
The adapter also exposes the same sanitized metadata as its latest invocation for
acceptance tooling. A response reporting a non-Flash model fails validation.

### Configuration

Active example/runtime configuration uses only:

```text
DEEPSEEK_MODEL=deepseek-v4-flash
```

`DEEPSEEK_REASONING_EFFORT` is removed from active examples and production runtime.
Docker Compose continues to consume `/etc/phe/runtime.env`; systemd continues to use
the same file. No MedGemma setting changes.

### Real acceptance without fabricated production health data

The VPS acceptance harness runs the deployed production code and real API credentials
against temporary copies of the databases. It exercises the real service/adapter paths
for Today, Q&A, and Context, then verifies the sanitized invocation metadata reports:

```text
model = deepseek-v4-flash
thinking = disabled
```

Feedback is `NOT_APPLICABLE` as an independent model path; its correction flow reuses
the Context operation already covered by the real call. Temporary state is removed
after validation.

After validation, `/etc/phe/runtime.env` is updated atomically, the container is rebuilt
and recreated, the systemd environment-file linkage and container effective environment
are checked, and production logs/database model identifiers are audited for zero Pro
calls after the cutover.

## Testing and Rollback

- Add request-payload tests before implementation to prove Flash plus explicit
  non-thinking behavior and absence of `reasoning_effort`.
- Add fail-closed and sanitized-audit tests.
- Update active real-gate expectations and run all Python and Flutter regressions.
- Back up the VPS runtime file and deployed code before the cutover.
- Rollback restores both artifacts together; a mixed Pro/Flash state is not accepted.

## Non-Goals

No changes to MedGemma, medical triggers/review, L1-L5, L6 reasoning semantics, L7
product contract, prompts, evidence assembly, or health-data persistence contracts.
