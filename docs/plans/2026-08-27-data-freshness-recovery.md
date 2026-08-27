# PHE Data Freshness Recovery Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Restore the daily health-data pipeline without weakening sealed checksums and make evidence age reflect the real Shanghai date.

**Architecture:** The existing definition reconciliation remains the only allowed production repair and becomes a fail-closed systemd preflight. Evidence rendering receives an explicit timezone-local reference date so a stalled pipeline cannot make old facts appear newer than they are.

**Tech Stack:** Python 3, pytest, SQLite sealed registries, systemd, FastAPI, Flutter regression tests, Alibaba Cloud Assistant.

---

### Task 1: Correct evidence freshness semantics

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/readers.py:352-454`
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/orchestrator.py:510-518`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/today.py:77-88`
- Test: `PersonalHealthEngine-L7/backend/tests/test_product_conformance.py`

**Step 1: Write the failing test**

Add a regression asserting that a 2026-08-20 evidence date is seven days old
on 2026-08-27, regardless of a stale 2026-08-24 analysis date.

```python
def test_freshness_uses_real_local_date_not_stale_analysis_date():
    days, label = readers.evidence_freshness("2026-08-20", "2026-08-27")
    assert days == 7
    assert label == "7 天前的数据"
```

**Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_product_conformance.py::test_freshness_uses_real_local_date_not_stale_analysis_date -q`

Expected: FAIL because `evidence_freshness` does not exist.

**Step 3: Implement the minimum behavior**

- Add `evidence_freshness(feature_date, reference_date)` in `readers.py`.
- Add a required keyword-only `freshness_date` to `exact_bundle_evidence` and use the helper.
- Pass `datetime.now(ZoneInfo(config.timezone_name)).date().isoformat()` from both callers.
- Refresh structured evidence labels when a persisted Today projection is read,
  without invoking a model or changing the semantic version.
- Keep future-dated evidence clamped to zero days, matching existing behavior.

**Step 4: Run focused and backend tests**

Run:

```powershell
python -m pytest tests/test_product_conformance.py -q
python -m pytest tests -q
```

Expected: all tests PASS.

**Step 5: Commit**

```powershell
git add PersonalHealthEngine-L7/backend
git commit -m "fix: calculate evidence freshness from local date"
```

### Task 2: Make definition reconciliation a daily preflight

**Files:**
- Modify: `deployment/systemd/phe-daily.service`
- Test: `deployment/tests/test_prepare_definition_files.py`

**Step 1: Write the failing test**

Add a static service test requiring the reconciliation command and requiring it
to appear before the daily pipeline `ExecStart`.

```python
def test_daily_service_reconciles_definitions_before_pipeline():
    service = (Path(__file__).parents[1] / "systemd" / "phe-daily.service").read_text()
    preflight = "ExecStartPre=/opt/phe/.venv/bin/python /opt/phe/deployment/scripts/prepare_definition_files.py --code-root /opt/phe --data-root /srv/phe"
    pipeline = "ExecStart=/opt/phe/.venv/bin/python /opt/phe/deployment/scripts/run_daily_pipeline.py"
    assert preflight in service
    assert service.index(preflight) < service.index(pipeline)
```

**Step 2: Run the test to verify it fails**

Run: `python -m pytest deployment/tests/test_prepare_definition_files.py::test_daily_service_reconciles_definitions_before_pipeline -q`

Expected: FAIL because the service lacks `ExecStartPre`.

**Step 3: Add the fail-closed preflight**

Add exactly one `ExecStartPre` line invoking `prepare_definition_files.py` with
the production code and data roots. Do not change the registry or weaken the
tool's non-EOL mismatch failure.

**Step 4: Run deployment tests**

Run: `python -m pytest deployment/tests -q`

Expected: 16 passed, 2 skipped (or higher if the suite grows).

**Step 5: Commit**

```powershell
git add deployment/systemd/phe-daily.service deployment/tests/test_prepare_definition_files.py
git commit -m "fix: reconcile sealed definitions before daily run"
```

### Task 3: Review and full local verification

**Files:**
- Modify: `docs/plans/task.md`

**Step 1: Review specification compliance**

Confirm every changed line maps to either real-date freshness or the checksum-safe preflight. Confirm no raw health data, registry mutation, or checksum bypass was added.

**Step 2: Review code quality**

Check timezone handling, call-site completeness, systemd ordering, and test clarity.

**Step 3: Run full verification**

Run backend tests, deployment tests, Flutter tests, Flutter analyze, `git diff --check`, and a secret-pattern scan of the diff.

Expected: tests PASS; analyzer has no errors or warnings; no secret values; clean diff.

**Step 4: Update tracker and commit**

Mark local tasks complete in `docs/plans/task.md` and commit the tracker/plan.

### Task 4: Recover and verify production

**Files:**
- Deploy the reviewed Git commit to `/opt/phe`
- Install: `deployment/systemd/phe-daily.service`

**Step 1: Back up operational state**

Record current Git commit, service result, L1 latest capture, and L5 maximum feature date. Do not print raw health values or secrets.

**Step 2: Deploy and reconcile**

Sync the reviewed source, run `prepare_definition_files.py`, require PASS for L3-L6, install the service unit, and run `systemctl daemon-reload`.

**Step 3: Rerun the daily service**

Run `systemctl start phe-daily.service` and wait on service state rather than an arbitrary long sleep.

**Step 4: Verify the full chain**

Require service `Result=success`, L1-L7 PASS evidence, L5 maximum date advancement, active/enabled timer, healthy public HTTPS, and protected private ports.

**Step 5: Verify user-facing freshness**

Refresh the authenticated Today projection and confirm the exact data date and freshness label are consistent with the current Shanghai date.
