# PHE Original Product Contract Conformance Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Restore the sealed PHE product presentation contract across Today, evidence, Q&A, history, patterns, context, production deployment, and the signed Android artifact without changing the current E judgment or SEALED upstream algorithms.

**Architecture:** Keep L6 as the reasoning owner, add an L7 Simplified-Chinese product adapter, centralize deterministic display labels, resolve evidence through exact L6 provenance IDs, and append a presentation-only Today repair while preserving semantic-version and notification rules.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, Flutter/Dart, Android Gradle, DeepSeek API, MedGemma/Ollama, Nginx/systemd, PowerShell/OpenSSH.

---

### Task 1: Lock presentation contracts with failing backend tests

**Files:**
- Create/modify: `PersonalHealthEngine-L7/backend/tests/test_product_conformance.py`
- Create/modify: `PersonalHealthEngine-L6/tests/test_l6_real_adapters_v0_1.py`

**Steps:**
1. Add focused tests for Chinese label mappings, no raw enums, distinct sleep labels, E/non-E ordering, exact provenance IDs, per-item date/freshness, stable-copy timestamp behavior, presentation-only migration, structured Q&A, and the real MedGemma protocol signature.
2. Run only those tests and capture failures caused by the audited defects.
3. Do not alter production code in this task.

### Task 2: Implement canonical labels and exact evidence provenance

**Files:**
- Create: `PersonalHealthEngine-L7/backend/l7/rendering/labels.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/rendering/renderer.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/readers.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/today.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/history.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/context.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/patterns.py`

**Steps:**
1. Add deterministic labels for every sealed/current machine vocabulary value.
2. Resolve exact L5/L3/L4 records from `reasoning_provenance`; never select a substitute by feature name.
3. Build structured Level 2 facts with specific feature label, direction, magnitude, date, and freshness; retain raw machine fields only for API compatibility.
4. Add display-label fields to history, context, patterns, and evidence detail.
5. Run focused tests, then self-review for contract coverage and unnecessary scope.

### Task 3: Enforce Chinese model output and repair Q&A protocol

**Files:**
- Modify: `PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/services/qna.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/api/app.py`

**Steps:**
1. Add an L7 product adapter that requires Simplified Chinese daily output, has a versioned cache identity, and uses a dedicated structured Chinese Q&A schema.
2. Restore the real MedGemma adapter's sealed protocol-compatible review signature.
3. Return separate Q&A answer, reason, and actions; never leak a raw enum or English fallback.
4. Run focused L6/L7 tests and review model-call accounting and fallback behavior.

### Task 4: Preserve semantics while repairing legacy Today presentation

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/engine/orchestrator.py`
- Modify: `PersonalHealthEngine-L7/backend/l7/rendering/renderer.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/test_product_conformance.py`

**Steps:**
1. Add a presentation contract version to rendered payloads.
2. On a legacy same-signature payload, translate/canonicalize once and append a presentation-only version with `judgment_updated=false` and no notification.
3. On later same-signature reads, preserve semantic copy and refresh only time fields.
4. Account for real model calls and verify retry/failure behavior cannot change health state.
5. Run focused tests and inspect the resulting SQLite version semantics.

### Task 5: Remove raw implementation values from Flutter

**Files:**
- Create/modify: `PersonalHealthEngine-L7/app/test/product_conformance_test.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/today_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/evidence_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/context_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/history_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/patterns_screen.dart`

**Steps:**
1. Add failing widget/unit assertions that representative raw enums and ambiguous `睡眠` facts are not displayed.
2. Render only explicit backend label fields and neutral Chinese fallbacks.
3. Show evidence value, baseline, date, and freshness without exposing maturity/status enums.
4. Run focused tests, `flutter analyze --no-fatal-infos`, and the full Flutter suite.

### Task 6: Run full local contract acceptance

**Files:**
- Modify: `docs/plans/task.md`
- Create: `docs/audits/2026-08-24-phe-contract-conformance.md`

**Steps:**
1. Run the formal L2-L7/deployment pytest baseline and all Flutter tests/analyzer checks.
2. Run deterministic no-English/no-enum payload scans over representative A-E fixtures and all user surfaces.
3. Record contract matches, contract gaps, upstream limitations, code fixes, model-output fixes, and unresolved external blockers.
4. Run the verification-before-completion checklist and final spec/code-quality reviews.

### Task 7: Deploy, accept production, and build the final APK

**Files:**
- Modify only deployment artifacts required by a reproduced deployment defect.
- Output: signed release APK under the repository's documented artifact path.

**Steps:**
1. Recover an authorized VPS path from existing local credentials without exposing secrets; otherwise record the exact missing-secret blocker.
2. Deploy the smallest backend change, restart/verify services, and exercise authenticated Today, evidence, Q&A, history, patterns, and context APIs.
3. Confirm production Today remains E for the same reason, is fully Chinese, uses exact evidence dates/IDs, and does not emit a false judgment update.
4. Build and verify the signed Android release APK, package ID, permissions, backup flags, signature, and production endpoint.

### Task 8: Secret audit, integrate, commit, and push

**Files:**
- Modify: `docs/plans/task.md`
- Modify: `docs/audits/2026-08-24-phe-contract-conformance.md`

**Steps:**
1. Audit tracked/untracked changes and scan for bearer tokens, API keys, keystore passwords, and private keys without printing secret values.
2. Run the complete final regression again from the branch.
3. Integrate the verified work into `main` without discarding unrelated user work.
4. Commit with `fix: restore PHE product contract conformance` and push `main`; report an authentication-only blocker if the remote rejects the push.
