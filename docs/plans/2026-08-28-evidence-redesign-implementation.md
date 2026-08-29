# Evidence Redesign Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Replace opaque evidence text and duplicate coverage-count cards with a fast, readable, metric-first experience covering eight health metrics and truthful freshness states.

**Architecture:** Extend the existing read-only `/evidence/today` projection with an `all_metrics` overview assembled from canonical L3 features, while preserving the existing `metrics` deviation list for compatibility. The Flutter app renders compact evidence facts from the existing Today payload and a filterable evidence screen from `all_metrics`; no health judgment is computed on-device.

**Tech Stack:** Python 3.12, FastAPI, SQLite read-only projections, pytest, Flutter/Dart, Material 3, flutter_test.

---

### Task 1: Define the all-metrics backend contract

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/tests/test_api_today.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/readers.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/today.py`

**Step 1: Write the failing test**

Add an API contract test requiring `all_metrics` to contain exactly these stable keys in display order: `steps`, `active_calories`, `sleep`, `heart_rate`, `resting_heart_rate`, `spo2`, `stress`, `workouts`. Assert every item exposes a friendly label, value or explicit unavailable state, source date, freshness status, judgment-use flag, and trend series. Assert no `bucket_count`, `L3`, `L4`, or `L5` text leaks into user-facing fields.

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_today.py::test_evidence_detail_includes_eight_readable_metric_overviews -q`

Expected: FAIL because `all_metrics` is absent.

**Step 3: Write minimal implementation**

Add a fixed presentation registry for the seven canonical L3 features plus the explicitly unavailable workout-record slot. Read only the latest current row and bounded recent series for each feature. Derive freshness labels from dates, mark participation by matching the exact bundle evidence feature names, and keep collection coverage counts out of the response.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_api_today.py -q`

Expected: PASS.

### Task 2: Redesign the Today evidence summary

**Files:**
- Modify: `PersonalHealthEngine-L7/app/test/widget_test.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/api_client.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/today_screen.dart`

**Step 1: Write the failing widget test**

Require the Today evidence card to show `本次判断参考了 1 项健康数据`, the friendly metric name, value, date/freshness, and `低于个人近期基线`. Assert the screen does not show provenance jargon or coverage-count labels.

**Step 2: Run test to verify it fails**

Run: `flutter test test/widget_test.dart --plain-name "Today evidence summary explains the data used in the judgment"`

Expected: FAIL because the current card prints opaque Level-2 strings.

**Step 3: Write minimal implementation**

Expose the structured `evidence` facts already present in Today, map canonical feature names to short Chinese labels, and render no more than two compact rows plus an explicit `查看全部健康指标` affordance. Preserve the existing tap target and Q&A/context entry buttons.

**Step 4: Run test to verify it passes**

Run the same targeted Flutter test.

Expected: PASS.

### Task 3: Build the filterable evidence detail experience

**Files:**
- Modify: `PersonalHealthEngine-L7/app/test/widget_test.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/evidence_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/widgets/trend_chart.dart`

**Step 1: Write failing widget tests**

Require a segmented filter for `本次使用`, `全部 8`, and `需更新`; verify all eight friendly metric names appear after selecting all. Require stale metrics to show their last data date and `数据需更新`, never `正常`. Require expansion of a metric row to show a unit-labelled trend, date range, and personal baseline label.

**Step 2: Run tests to verify they fail**

Run: `flutter test test/widget_test.dart --plain-name "Evidence"`

Expected: FAIL because the current screen only renders deviating metrics as technical cards.

**Step 3: Write minimal implementation**

Parse `all_metrics`, add the three filters, render compact accessible metric rows, and expand rows with series/baseline details. Use Material icons and existing project colors; do not add a chart dependency.

**Step 4: Run tests to verify they pass**

Run the same targeted Flutter tests.

Expected: PASS.

### Task 4: Regression, visual, and package verification

**Files:**
- Create: `design-qa.md`
- Update: `docs/plans/task.md`

**Step 1: Run backend tests**

Run: `python -m pytest -q`

Expected: all backend tests pass.

**Step 2: Run Flutter checks**

Run: `flutter analyze`

Run: `flutter test`

Expected: no analyzer errors and all widget/unit tests pass.

**Step 3: Build the APK**

Run: `flutter build apk --release`

Expected: exit code 0 and a release APK under `build/app/outputs/flutter-apk/`.

**Step 4: Perform visual QA**

Capture the redesigned Today and Evidence screens at the target mobile viewport, compare them with the selected metric-first concept and original screenshots, record findings in `design-qa.md`, and fix all P0/P1/P2 findings before handoff.

**Step 5: Record delivery state**

Add a concise row to `docs/plans/task.md` with test results, APK path/hash, deployment state, and any external quota limitation affecting the Figma mirror.
