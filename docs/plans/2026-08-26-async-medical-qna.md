# Async Medical Q&A Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Make long decision and medical Q&A non-blocking without exposing an unreviewed health conclusion.

**Architecture:** Deterministic fast scopes remain synchronous. All other Q&A is committed to the existing SQLite durable queue, processed by its single worker, and returned through authenticated polling.

**Tech Stack:** FastAPI, SQLite, Python worker, Flutter/Dart, pytest, flutter_test

---

### Task 1: Durable Q&A result contract

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/jobs.py`
- Test: `PersonalHealthEngine-L7/backend/tests/test_jobs.py`

1. Write failing tests for `QA_ASK`, result visibility after success, ownership, and deduplication.
2. Run `python -m pytest PersonalHealthEngine-L7/backend/tests/test_jobs.py -q` and verify RED.
3. Add `QA_ASK` and return parsed `result_json` only for succeeded jobs.
4. Run the focused tests and verify GREEN.

### Task 2: API routing and worker execution

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/api/app.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/worker.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/__init__.py`
- Test: `PersonalHealthEngine-L7/backend/tests/test_api_qna.py`
- Test: `PersonalHealthEngine-L7/backend/tests/test_worker.py`

1. Write failing tests proving fast scopes return 200, decision scopes commit and return 202, and the worker completes the authoritative Q&A result.
2. Run the focused tests and verify RED.
3. Add the minimal deterministic routing, enqueue path, worker branch, and version bump.
4. Run the focused tests and verify GREEN.

### Task 3: Flutter polling UX

**Files:**
- Modify: `PersonalHealthEngine-L7/app/lib/api_client.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/qa_screen.dart`
- Test: `PersonalHealthEngine-L7/app/test/qa_screen_test.dart`

1. Write failing widget/client tests for 202 polling, completed rendering, retry-key reuse, and failure UX.
2. Run the focused Flutter tests and verify RED.
3. Implement one idempotency key per user submission and poll the existing job endpoint without showing a candidate.
4. Run the focused tests and verify GREEN.

### Task 4: Production acceptance

**Files:**
- Update: `PHE_PERFORMANCE_AUDIT.json`
- Update: `docs/plans/task.md`

1. Run full Python, Flutter, analyzer, deployment, and secret checks.
2. Build and verify the signed APK.
3. Deploy the pinned commit, measure 202 acknowledgement and read latency during MedGemma, reboot, and verify services and private ports.
4. Record only measured outcomes, commit, push main, and verify a clean worktree.
