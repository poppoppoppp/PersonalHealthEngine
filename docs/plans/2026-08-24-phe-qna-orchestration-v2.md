# PHE Q&A Orchestration V2 Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Replace keyword-authoritative Q&A routing with DeepSeek semantic understanding, question-specific personal evidence, post-candidate risk-gated MedGemma review, and deterministic finalization.

**Architecture:** Extend the existing L7 product adapter over the sealed Flash transport, add a focused deterministic Q&A orchestration module, keep conversation lifecycle in `QnAService`, and add an L7 audit migration. Preserve all non-Q&A product contracts.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, Flutter/Dart, DeepSeek V4 Flash, MedGemma 1.5/Ollama.

---

### Task 1: Lock semantic routing and follow-up behavior

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/tests/test_qna.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/conftest.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py`
- Create: `PersonalHealthEngine-L7/backend/l7/engine/qna_orchestration.py`

1. Add failing tests for the five required semantic examples and the “那半小时呢？” follow-up.
2. Run the focused tests and confirm they fail because semantic classification is absent.
3. Add strict semantic schema validation and the Flash-backed classifier method.
4. Add a deterministic mock classifier only for automated tests.
5. Run the focused tests and confirm they pass.

### Task 2: Implement deterministic health-data authority

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/tests/test_qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/readers.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/qna_orchestration.py`

1. Add failing tests for last-night sleep and multi-day aggregation provenance.
2. Confirm the current model-only path fails the expected assertions.
3. Add the canonical L3 metric registry and deterministic latest/average/trend queries.
4. Format Chinese answers from exact engine values without model arithmetic.
5. Run focused tests green.

### Task 3: Build question-specific evidence and candidate validation

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/tests/test_qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/qna_orchestration.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py`

1. Add failing tests proving irrelevant bundle domains/patterns are excluded and refs are traceable.
2. Add failing hallucination tests for unsupported fever and invalid evidence refs.
3. Implement deterministic bundle selection, evidence catalog creation, strict candidate schema, and hallucination validation.
4. Add the Flash candidate method using the selected bundle only.
5. Run focused tests green.

### Task 4: Move medical review after the candidate and finalize safely

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/tests/test_qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/qna_orchestration.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py`
- Modify: `PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py`
- Modify: `PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py`

1. Add failing ordering and review-input tests.
2. Add failing tests for APPROVED, APPROVED_WITH_CHANGES, REJECTED, ESCALATE, and UNAVAILABLE.
3. Add failing unsafe-candidate and unavailable-review tests.
4. Implement the combined deterministic consequence gate and post-candidate review bundle.
5. Implement strict review validation and deterministic finalizer paths, with constrained DeepSeek correction only for APPROVED_WITH_CHANGES.
6. Run focused L6/L7 tests green.

### Task 5: Integrate conversation, context, persistence, and model-call audit

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/services/qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/context.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/api/app.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/store/db.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/test_qna.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/test_api_phase_e.py`

1. Add failing audit-migration, call-accounting, context-authority, and API-shape tests.
2. Add migration 2 with `qna_audits` and sanitized structured fields.
3. Route every non-empty question through Stage A, then the deterministic branch.
4. Preserve conversation lifecycle and pass bounded prior turns only as semantic context.
5. Invoke formal Context write semantics only for explicit high-confidence user facts.
6. Persist L6 QA/medical rows and the L7 orchestration audit without secrets or reasoning traces.
7. Run focused backend tests green.

### Task 6: Add explicit Flutter processing states

**Files:**
- Modify: `PersonalHealthEngine-L7/app/lib/screens/qa_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/test/widget_test.dart`

1. Add a failing widget test for staged loading copy and explicit retryable failure.
2. Implement bounded staged loading text without exposing model names.
3. Confirm widget tests and analyzer pass.

### Task 7: Regression, production deployment, and release

**Files:**
- Modify: `PersonalHealthEngine-L7/L7_TECHNICAL_ARCHITECTURE.md`
- Modify: `PersonalHealthEngine-L7/L7_FINAL_AUDIT.json`
- Create: `PersonalHealthEngine-L7/docs/PHE_QNA_ORCHESTRATION_V2_AUDIT.json`
- Modify: `docs/plans/task.md`

1. Run focused tests, full backend tests, L6 tests, model static audit, and upstream integrity.
2. Run Flutter analyze and all Flutter tests.
3. Build and verify the signed production APK because loading UX changed.
4. Run the repository secret audit and inspect the complete diff.
5. Deploy to `/opt/phe`, verify services/TLS/protected ports, and run the five authenticated real Q&A cases with audit-order evidence.
6. Re-run Today, History, Patterns, Context, Feedback, daily service, and timer acceptance.
7. Record actual evidence in the audit artifact and sealed documents.
8. Commit `feat: upgrade PHE Q&A semantic and medical orchestration` and push.
