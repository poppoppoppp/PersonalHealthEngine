# L7 HANDOFF — Personal Health Engine, Layer 7 (Product Output)

Status: **L7 = SEALED** (see `L7_SEAL.md`, audit `L7_FINAL_AUDIT.json`).
Date: 2026-08-18

## 1. What you have now

A complete personal-health product layer on top of the sealed L1–L6 engine:

- **Backend** (`backend/`, Python 3.14 + FastAPI + SQLite WAL): serves the Current Today
  State, Q&A decision assistant, Context capture, Feedback loop, History episodes,
  Personal Patterns, and the Notification gate. The app never computes health state.
- **App** (`app/`, Flutter): Android-first with Web build. Four tabs 今日/历史/我的规律/我的.
- **Ops**: Docker packaging, backup/retention, per-user export/delete, deployment guide.

## 2. Run it locally (what was used all along)

```powershell
# 1) Backend (from D:\PersonalHealthEngine-L7\backend)
$env:L7_ENV='local'
$env:L7_REASONING_ADAPTER='deepseek'   # or 'mock' for zero-cost runs
$env:L7_MEDICAL_ADAPTER='medgemma'     # or 'mock'
$env:DEEPSEEK_API_KEY=[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')
python -m uvicorn l7.api.app:create_app --factory --host 127.0.0.1 --port 8707

# 2) Tests
python -m pytest tests -q            # 89 tests

# 3) App (from D:\PersonalHealthEngine-L7\app)
flutter analyze && flutter test      # 0 errors / 5 widget tests
# install app/build/app/outputs/flutter-apk/app-debug.apk on the phone,
# or serve app/build/web statically; set server URL in the 我的 tab
# (Android emulator uses http://10.0.2.2:8707, dev token 'dev-local-token' locally)
```

Flutter on this machine hangs via the `.bat` wrappers; so invoke:
`D:\flutter\flutter\bin\cache\dart-sdk\bin\dart.exe D:\flutter\flutter\bin\cache\flutter_tools.snapshot --no-version-check <cmd>`
with `CI=true`, `FLUTTER_SUPPRESS_ANALYTICS=true`, `FLUTTER_ROOT=D:\flutter\flutter`.

## 3. Run it in production

See `DEPLOYMENT.md`. Summary: one VPS, `docker compose up -d --build`, sealed L3–L6
mounted read-only, `.env` holds `L7_API_TOKEN` + `DEEPSEEK_API_KEY`, TLS in front,
point the app at the VPS URL. Docker is not installed on the dev PC, so this final
`compose up` is the one step that happens on the VPS.

## 4. Cost & safety model (what to protect when extending)

| Threshold | Meaning | Trigger cost |
|---|---|---|
| Recompute | upstream signature changed | bundle assembly (free) |
| Model call | bundle hash changed | exactly 1 DeepSeek call (cached by request hash otherwise) |
| UI Change | judgment signature changed | new Today version, no model |
| Notification | new judgment version AND mode gate | deterministic, never a model call |

- Medical review (MedGemma) only on the sealed trigger policy (symptom context or
  disease/doctor questions); safety overrides quiet hours and notification mode.
- Out-of-scope Q&A and insufficient-evidence answers cost 0 model calls.

## 5. Where things live (map)

```
backend/l7/config.py               env-driven config; prod refuses boot without token
backend/l7/store/db.py             L7 db schema (users, today_versions, eval_runs, cache,
                                   conversations, qa_turns, context_time_meta, settings,
                                   notification_decisions, episodes...)
backend/l7/upstream/               read-only readers + L6Bridge (import-only seam)
backend/l7/engine/orchestrator.py  three-threshold evaluation; judgment_listeners hook
backend/l7/engine/model_cache.py   provenance-aware model-call cache
backend/l7/rendering/renderer.py   deterministic five-state renderer (no model in path)
backend/l7/services/               today, qna, context, feedback, history, notify
backend/l7/api/app.py              routes + bearer auth + local-only CORS
backend/scripts/backup.py          VACUUM INTO snapshots + retention
backend/scripts/verify_upstream_integrity.py   seal guardian: SEALED layers untouched
backend/l7/admin/export_delete.py  per-user export/delete
app/lib/                           Flutter app (api_client + screens + widgets)
app/bin/e2e_check.dart             live-API transport check (18 checks)
Dockerfile / docker-compose.yml    cloud packaging
DEPLOYMENT.md                      VPS runbook
```

## 6. Known limitations (inherited consciously)

1. **Language**: real DeepSeek reasoning summaries are English (sealed L6 prompt does not
   force zh); L7 shows model text verbatim rather than re-paraphrasing (semantic-stability
   rule). Forcing zh-CN = sealed-L6 prompt change → future interface extension, needs the
   layer owner's approval.
2. **Q&A question budget** (one valuable pending question) returns none until a learning
   loop over patterns/feedback history exists.
3. **OS push**: MVP transport is the in-app feed + decision audit; Android local
   notifications are a thin follow-up (gate logic is transport-agnostic).
4. **History search** is deterministic keyword match.
5. **APK** is debug-signed; release signing for store distribution is pending.
6. **Data volume**: one analysis date exists upstream today; multi-day behavior is proven
   on seeded test copies and will light up naturally as L1–L6 accumulate days.

## 7. Extension protocol after the seal

- Read `L7_SEAL.md` "Breaking the seal" first.
- Never edit SEALED L1–L6; if blocked, propose a minimal interface extension.
- Tests before code; phase-audit addendum after; re-run
  `scripts/verify_upstream_integrity.py` before declaring anything done.
- Keep mocks as the default test adapters; gate every real model call and record it.
