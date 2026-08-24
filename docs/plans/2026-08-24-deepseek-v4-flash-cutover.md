# DeepSeek V4 Flash Production Cutover Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Make every real PHE DeepSeek request use `deepseek-v4-flash` with explicit non-thinking mode, prove each production path with sanitized audit evidence, and cut over the VPS atomically.

**Architecture:** Enforce model and thinking configuration once in the shared L6 real adapter, with L7 passing only an operation label. Emit sanitized successful-invocation metadata and validate real Today, Q&A, and Context paths against temporary database copies before changing the live runtime.

**Tech Stack:** Python 3.12, pytest, FastAPI/L7 services, urllib DeepSeek transport, Docker Compose, systemd, SQLite, Flutter.

---

### Task 1: Specify the shared DeepSeek transport contract

**Files:**
- Modify: `PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py`
- Test: `PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py`

**Step 1: Write failing tests**

Add tests that monkeypatch `_post_json` and assert:

```python
assert payload["model"] == "deepseek-v4-flash"
assert payload["thinking"] == {"type": "disabled"}
assert "reasoning_effort" not in payload
assert adapter.last_invocation["response_model"] == "deepseek-v4-flash"
```

Also assert a non-Flash configured model fails before `_post_json`, and audit metadata contains no API key, prompt, or response content.

**Step 2: Run tests to verify they fail**

Run: `python -m pytest PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py -q`

Expected: failures showing the Pro default, missing `thinking`, present `reasoning_effort`, and missing audit metadata.

**Step 3: Commit the red tests**

```bash
git add PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py
git commit -m "test: specify DeepSeek Flash transport contract"
```

### Task 2: Implement the fail-closed Flash transport

**Files:**
- Modify: `PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py`
- Test: `PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py`

**Step 1: Implement the minimal transport change**

- Set `DEEPSEEK_MODEL_DEFAULT = "deepseek-v4-flash"`.
- Remove the adapter's reasoning-effort state and `_chat` argument.
- Add an operation argument to `_chat`.
- Reject any configured model other than the Flash constant before network I/O.
- Send `"thinking": {"type": "disabled"}` and no `reasoning_effort`.
- Validate `response["model"]` is Flash.
- Store and log sanitized `last_invocation` metadata only after success.
- Label base adapter calls `today`, `qna`, and `context`.

**Step 2: Run the focused tests**

Run: `python -m pytest PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py -q`

Expected: all tests pass.

**Step 3: Commit**

```bash
git add PersonalHealthEngine-L6/scripts/l6_real_adapters_v0_1.py PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py
git commit -m "feat: enforce DeepSeek V4 Flash non-thinking transport"
```

### Task 3: Route all L7 operations through the new contract

**Files:**
- Modify: `PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py`
- Modify: `PersonalHealthEngine-L7/backend/tests/test_product_conformance.py`
- Test: `PersonalHealthEngine-L7/backend/tests/test_product_conformance.py`

**Step 1: Update tests first**

Make the fake `_chat` accept `operation` and assert Today uses `today`, Q&A uses `qna`, and product repair uses `product_translation`. Add a regression that the product adapter exposes the configured Flash identifier.

**Step 2: Run tests to verify failure**

Run: `python -m pytest PersonalHealthEngine-L7/backend/tests/test_product_conformance.py -q`

Expected: failures from the old `reasoning_effort` calls.

**Step 3: Implement minimal L7 routing**

Replace all `reasoning_effort` arguments with fixed operation labels. Keep prompts, JSON schemas, Chinese validation, and Context delegation unchanged.

**Step 4: Run focused L6/L7 tests**

Run: `python -m pytest PersonalHealthEngine-L6/tests/test_l6_real_adapter_runtime.py PersonalHealthEngine-L7/backend/tests/test_product_conformance.py -q`

Expected: all tests pass.

**Step 5: Commit**

```bash
git add PersonalHealthEngine-L7/backend/l7/upstream/l6_bridge.py PersonalHealthEngine-L7/backend/tests/test_product_conformance.py
git commit -m "fix: route L7 DeepSeek calls through Flash contract"
```

### Task 4: Align active configuration and static audit gates

**Files:**
- Modify: `PersonalHealthEngine-L6/.env.example`
- Modify: `deployment/config/runtime.env.example`
- Modify: `PersonalHealthEngine-L6/scripts/l6_adapters_v0_1.py`
- Modify: `PersonalHealthEngine-L7/backend/scripts/validate_real_reasoning_gate.py`
- Modify: `PersonalHealthEngine-L7/L7_TECHNICAL_ARCHITECTURE.md`
- Create: `PersonalHealthEngine-L7/backend/scripts/audit_deepseek_model_config.py`
- Create: `PersonalHealthEngine-L7/backend/tests/test_deepseek_model_audit.py`

**Step 1: Write the failing static audit test**

The audit scans active Python/config/deployment paths and fails on:

```text
deepseek-v4-pro
DEEPSEEK_REASONING_EFFORT
reasoning_effort
```

It requires the Flash identifier and explicit disabled-thinking payload. Historical sealed audit artifacts are excluded from the active-production scan.

**Step 2: Run the audit test and confirm failure**

Run: `python -m pytest PersonalHealthEngine-L7/backend/tests/test_deepseek_model_audit.py -q`

Expected: failure listing current active Pro/effort references.

**Step 3: Update active defaults, gates, mock identity, and architecture wording**

Remove the reasoning-effort example setting, switch active model identifiers and gates to Flash, and document one non-thinking transport for all surfaces. Do not modify historical evidence reports.

**Step 4: Run focused static and semantic tests**

Run: `python -m pytest PersonalHealthEngine-L7/backend/tests/test_deepseek_model_audit.py PersonalHealthEngine-L6/tests PersonalHealthEngine-L7/backend/tests -q`

Expected: all tests pass.

**Step 5: Commit**

```bash
git add PersonalHealthEngine-L6 PersonalHealthEngine-L7 deployment/config/runtime.env.example
git commit -m "chore: align active DeepSeek configuration with Flash"
```

### Task 5: Add real-call acceptance harness

**Files:**
- Create: `PersonalHealthEngine-L7/backend/scripts/validate_deepseek_flash_paths.py`
- Create: `PersonalHealthEngine-L7/backend/tests/test_validate_deepseek_flash_paths.py`

**Step 1: Write harness unit tests first**

Test report classification, secret redaction, temporary-directory cleanup, Today/Q&A/Context operation coverage, and Feedback `NOT_APPLICABLE` classification.

**Step 2: Run tests to verify failure**

Run: `python -m pytest PersonalHealthEngine-L7/backend/tests/test_validate_deepseek_flash_paths.py -q`

Expected: import/file-not-found failure.

**Step 3: Implement the harness**

Use temporary SQLite copies and production services/adapters. Run one real call per required path and print only the exact model/thinking/operation acceptance fields plus non-sensitive usage. Always clean up temporary state.

**Step 4: Run harness unit tests**

Run: `python -m pytest PersonalHealthEngine-L7/backend/tests/test_validate_deepseek_flash_paths.py -q`

Expected: all tests pass without a real key.

**Step 5: Commit**

```bash
git add PersonalHealthEngine-L7/backend/scripts/validate_deepseek_flash_paths.py PersonalHealthEngine-L7/backend/tests/test_validate_deepseek_flash_paths.py
git commit -m "test: add real DeepSeek Flash path acceptance"
```

### Task 6: Run complete local regression and publish code

**Files:**
- Modify only files needed to fix failures caused by the cutover.

**Step 1: Run all Python tests**

Run: the repository's established full Python test command.

Expected: at least the previous `184 passed, 2 skipped`, plus the new tests.

**Step 2: Run Flutter tests and analyzer**

Run: `flutter test` and `flutter analyze` in `PersonalHealthEngine-L7/app`.

Expected: 31 or more tests pass; zero analyzer errors and warnings.

**Step 3: Run static production-path audit and diff checks**

Run: `python PersonalHealthEngine-L7/backend/scripts/audit_deepseek_model_config.py`, `git diff --check`, and `git status --short`.

Expected: model audit PASS, no whitespace errors, only intended files.

**Step 4: Commit residual verified changes and push**

```bash
git push origin main
```

Expected: remote main contains the verified cutover commits.

### Task 7: Deploy and prove the VPS cutover

**Files:**
- Runtime-only: `/etc/phe/runtime.env`
- Deployment: `/opt/phe`

**Step 1: Capture sanitized pre-cutover evidence and backups**

Record deployed commit, container model/effort values, systemd environment files, health, and backups without reading the API key.

**Step 2: Deploy the pinned Git commit**

Overlay the pinned release while preserving `/opt/phe/.venv`, rebuild the image, and leave the current container running until the new image is ready.

**Step 3: Atomically update production runtime**

Replace only the DeepSeek model line with `DEEPSEEK_MODEL=deepseek-v4-flash`, remove `DEEPSEEK_REASONING_EFFORT`, preserve file ownership/mode, recreate the container, and run `systemctl daemon-reload` only if unit files changed.

**Step 4: Validate effective environment**

Check `/etc/phe/runtime.env`, Docker Compose resolved environment, running-container environment, and systemd environment-file linkage. Never print `DEEPSEEK_API_KEY`.

**Step 5: Execute the real Flash path harness**

Run the deployed harness inside the production container/environment with temporary DB copies.

Expected:

```text
TODAY REAL FLASH CALL = PASS
Q&A REAL FLASH CALL = PASS
CONTEXT REAL FLASH CALL = PASS
FEEDBACK REAL FLASH CALL = NOT_APPLICABLE
```

**Step 6: Audit production invocation records**

Confirm all post-cutover successful DeepSeek audit records report `deepseek-v4-flash`, thinking disabled, and zero Pro identifiers. Confirm MedGemma configuration and identity are unchanged.

**Step 7: Verify health, daily automation, and mobile/public access**

Check local container health, public HTTPS health/authenticated read path, timer/service state, and an authenticated mobile-compatible Today read. Diagnose and repair any gateway issue before final acceptance.

**Step 8: Clean temporary artifacts and report rollback path**

Remove only validated staging/temp files and retain named backups for recovery.
