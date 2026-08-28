# Today Degraded Reasoning Recovery Design

## Problem

The 2026-08-27 daily refresh persisted the safe fallback text after a transient
DeepSeek failure. DeepSeek is available again, but unchanged upstream evidence
causes later refreshes to reuse the degraded projection forever.

Separately, the Xiaomi collector is healthy but the latest 2026-08-26 through
2026-08-28 capture returned zero heart-rate, resting-heart-rate, SpO2, and stress
records. Those metrics must remain dated 2026-08-20; the product must not invent
freshness that the source did not provide.

## Approved Approach

Keep ordinary `GET /today` reads model-free and fast. During the existing
background `POST /today/refresh` path only, detect the known degraded reasoning
summary and retry DeepSeek once against the exact current evidence bundle.

On a valid response, reuse the existing deterministic hypothesis, confidence,
product state, evidence IDs, and L6 daily-reasoning ID. Replace only the L7
presentation summary and actions, append a new Today version, and state that the
reasoning explanation recovered without a health-judgment change. Do not modify
or delete sealed L6 history.

If the retry fails, returns invalid Chinese product JSON, or changes the current
primary/secondary hypothesis, keep the existing safe projection unchanged.

## Alternatives Rejected

- Retry on every app open: rejected because it would increase latency and cost.
- One-off production database editing: rejected because it is not durable and
  would bypass append-only provenance.
- Pretend all metrics are current: rejected because Xiaomi returned no new
  records for several sensors.

## Verification

- A regression test proves app-open reads never call the model.
- A regression test proves manual refresh replaces a degraded projection after
  a valid model recovery while keeping the same judgment and L6 reasoning ID.
- A regression test proves failed or hypothesis-changing recovery leaves the
  degraded projection unchanged.
- Full backend tests run before deployment.
- Production verification checks the new Today text, model-call audit, response
  latency boundary, metric dates, service health, and protected ports.
