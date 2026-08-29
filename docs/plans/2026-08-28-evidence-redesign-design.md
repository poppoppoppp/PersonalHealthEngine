# Personal Health Engine evidence redesign

## Goal

Make health evidence understandable to ordinary users while preserving traceability for developers. The product must separate health measurements, data freshness, judgment participation, and collection completeness.

## Approved direction

The user selected the metric-first direction (option 2). Every metric row must expose the metric name, current value and unit, source date, freshness state, and whether it participated in today's judgment.

## Screens

1. **Today evidence summary**: concise explanation of which metrics support the current judgment and which are stale or unavailable.
2. **Evidence detail**: defaults to the metrics used in the judgment and shows readable trends, baseline comparison, units, dates, and caveats.
3. **All health metrics**: lists steps, active energy, sleep, heart rate, resting heart rate, blood oxygen, stress, and workout records with freshness and participation status.

## Safety and data semantics

- Collection record counts describe ingestion completeness only; they never represent activity quantity or health state.
- Stale metrics are labelled by their last data date and cannot be described as normal or abnormal for today.
- Trend charts include units, date range, and a labelled personal baseline.
- The interface avoids internal terms such as `bucket_count`, L3/L4/L5, and structured evidence.
- Health statements remain non-diagnostic and identify limitations explicitly.

## Visual system

- Preserve the Flutter Material 3 visual language.
- Primary blue: `#33557A`; scaffold background: `#F7F8FA`.
- White and pale-blue surfaces, restrained amber freshness warnings, 10–16 px radii, 16–20 px layout spacing.
- Chinese UI typography uses Noto Sans SC; numeric data remains compatible with Roboto.
- Repeated metric rows, chips, buttons, segmented controls, and charts are reusable Figma components bound to local variables.

## Acceptance criteria

- A user can identify a metric's value, date, freshness, and judgment use within three seconds.
- All eight requested health metrics are present in the full view.
- No stale value is represented as current or normal.
- No collection-coverage count is presented as a health result.
- The Figma file contains three 390 × 844 mobile frames, reusable components, design variables, documentation, and QA screenshots.
