# Evidence redesign visual QA

Target viewport: 390 × 844 logical pixels.

Source references:

- Original Today evidence summary screenshot supplied by the user.
- Original evidence-detail screenshot supplied by the user.
- Approved metric-first visual direction (option 2).

Implementation captures:

- `PersonalHealthEngine-L7/app/test/goldens/today_evidence_summary.png`
- `PersonalHealthEngine-L7/app/test/goldens/evidence_used.png`
- `PersonalHealthEngine-L7/app/test/goldens/evidence_all.png`

## Findings

- P0: none.
- P1: none.
- P2: none after fixing the narrow-screen overflow in the Today evidence header.
- P3: the complete eight-item list requires vertical scrolling by design; the filter counts make the hidden remainder explicit.

## Verified improvements

- Coverage-count evidence and L3/L4/L5 provenance language are absent from the user interface.
- The Today card names each health metric, value, freshness, and baseline direction.
- Duplicate facts for the same feature collapse to one visible row.
- The detail view separates metrics used in the judgment from all collected metrics and metrics needing refresh.
- Stale data uses an amber freshness label with its last date; it is never labelled normal.
- Trend expansion includes unit, date range, and a labelled personal baseline when available.
- The 390 px layout has no overflow after the final pass.

final result: passed
