# Today Degraded Reasoning Recovery Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Let an explicit background Today refresh recover a persisted DeepSeek fallback without slowing ordinary app-open reads or changing sealed health evidence.

**Architecture:** Detect the exact degraded reasoning marker only on `manual_refresh`. Re-run the existing product DeepSeek adapter against the unchanged evidence bundle, accept the result only when it validates and preserves the stored primary/secondary hypotheses, then append an L7 Today presentation version using the same L6 reasoning ID and judgment signature. A failed retry leaves the current projection untouched.

**Tech Stack:** Python 3.14, FastAPI service layer, SQLite append-only projections, pytest.

---

### Task 1: Lock the recovery contract with failing integration tests

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/tests/test_change_detection.py`

**Step 1: Write the failing app-open and manual-refresh test**

Add a test that changes the copied CURRENT L6 daily reasoning summary to the known
fallback marker, calls `evaluate(..., "app_open")`, and asserts zero model calls.
Then call `evaluate(..., "manual_refresh")` and assert:

- one reasoning call;
- fallback text is absent;
- `judgment_updated` remains false;
- a new Today version is appended;
- both Today versions reference the same L6 daily-reasoning ID.

**Step 2: Write the failing fail-closed test**

Use an adapter whose `reason_daily` raises. Assert a manual refresh returns the
existing fallback payload and does not append a Today version.

**Step 3: Run tests to verify RED**

Run:

```powershell
python -m pytest tests/test_change_detection.py -q
```

Expected: the recovery assertions fail because unchanged bundles never retry the model.

**Step 4: Commit the red tests**

```powershell
git add PersonalHealthEngine-L7/backend/tests/test_change_detection.py
git commit -m "test: reproduce sticky Today reasoning fallback"
```

### Task 2: Implement guarded L7 presentation recovery

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/orchestrator.py`
- Test: `PersonalHealthEngine-L7/backend/tests/test_change_detection.py`

**Step 1: Add exact degraded-state detection**

Define one module-level fallback marker and a small predicate that checks the stored
reasoning summary or rendered cause text. Do not match arbitrary empty or stale text.

**Step 2: Add the guarded retry helper**

The helper must:

- run only for `manual_refresh`;
- regenerate deterministic candidates from the exact current bundle;
- call the existing reasoning adapter once, using the existing request/cache contract;
- validate Chinese product JSON through the existing adapter and L6 validator;
- reject output that changes stored primary or secondary hypotheses;
- copy only `reasoning_summary` and `recommended_actions_json` into a display-only row;
- return the original row unchanged on every failure.

**Step 3: Append a recovered Today projection**

In both unchanged-signature rendering paths, bypass the normal fast return only when
the manual refresh has a degraded projection. On successful recovery, render and append
a Today version with the original L6 reasoning ID, bundle hash, product state, and
judgment signature. Set `judgment_updated=false` and use the change note
`推理说明已恢复，健康判断未改变。`.

**Step 4: Run focused tests to verify GREEN**

Run:

```powershell
python -m pytest tests/test_change_detection.py -q
```

Expected: all change-detection tests pass.

**Step 5: Commit the implementation**

```powershell
git add PersonalHealthEngine-L7/backend/l7/engine/orchestrator.py
git commit -m "fix: recover degraded Today reasoning on refresh"
```

### Task 3: Run regressions and integrate

**Files:**
- Modify: `docs/plans/task.md`

**Step 1: Run the full backend suite**

```powershell
python -m pytest -q
```

Expected: all backend tests pass.

**Step 2: Run static and secret checks**

```powershell
git diff --check main...HEAD
git diff main...HEAD | rg -i "api[_-]?key|bearer +[A-Za-z0-9]"
```

Expected: diff check passes and no credential value is present.

**Step 3: Update the task tracker and commit**

Record the test counts and keep the Xiaomi zero-record finding separate from the
reasoning recovery status.

**Step 4: Merge the feature branch into main and push**

Use a non-destructive merge, rerun the focused test on merged main, and push only after
the merged test passes.

### Task 4: Deploy and verify production

**Files:**
- Deploy: `PersonalHealthEngine-L7/backend/l7/engine/orchestrator.py`

**Step 1: Back up production L7 state and deploy the exact Git commit**

Use SQLite online backup for `/srv/phe/l7/db/l7_product.sqlite3`, rebuild the backend and
worker containers, and wait for `/health`.

**Step 2: Trigger one authenticated background Today refresh**

Call `POST /today/refresh` from the server with the existing environment token. Do not
print the token or health values.

**Step 3: Verify the original symptom**

Read `GET /today` and print only analysis/data dates, cause fallback presence, version
ID, model-call count, and update time. Expected:

- fallback marker absent;
- evidence dates remain truthful;
- a new version exists with the same L6 reasoning ID;
- ordinary repeated `GET /today` does not make another model call.

**Step 4: Verify production boundaries**

Confirm HTTPS health 200, daily timer active, and public ports 8707/11434 closed.

