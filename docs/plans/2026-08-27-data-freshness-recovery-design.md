# PHE Data Freshness Recovery Design

## Problem

The 2026-08-27 daily production run completed Xiaomi collection (L1) and import
(L2), then failed at L3 `POINT heart_rate`. The sealed L3 registry rejected the
deployed definition because its bytes differed from the registered checksum.
The repository already contains a fail-closed reconciliation tool that can
repair CRLF-only transport drift without changing registry rows or health data.

Separately, evidence freshness is calculated relative to the latest analysis
date. When the pipeline stalls, a 2026-08-20 fact is therefore shown as four
days old because the stale analysis date is 2026-08-24, even though the real
local date is 2026-08-27.

## Approved Approach

1. Keep the sealed checksum guard unchanged.
2. Run `prepare_definition_files.py` before every daily pipeline. It may rewrite
   only path/EOL transport defects whose repaired bytes exactly match the
   ACTIVE registry checksum; all other drift remains a hard failure.
3. Compute evidence age against the current date in the configured
   `Asia/Shanghai` timezone, while continuing to show the exact source date.
4. Repair production definitions with the same reconciliation tool, rerun the
   daily service, and verify L1-L7 plus public Today freshness.

## Data and Safety Boundaries

- No raw health record or definition registry row is modified by the repair.
- No checksum is regenerated or bypassed.
- L3-L7 continue to fail closed on non-EOL definition changes.
- The UI does not claim that a manual refresh collected new wearable data.
- Production verification reports exact layer dates and service outcomes,
  without printing tokens or raw health values.

## Verification

- A regression test proves 2026-08-20 is seven days old on 2026-08-27 even
  when the latest analysis date remains 2026-08-24.
- A deployment test proves the fail-closed reconciliation runs before the daily
  pipeline.
- Backend, deployment, and Flutter regression suites remain green.
- Production definition reconciliation passes, `phe-daily.service` succeeds,
  L5 advances, and the public application reports the corrected data date.
