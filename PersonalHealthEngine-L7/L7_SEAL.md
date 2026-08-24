# L7 SEAL — Layer 7 (Product Output) = SEALED

Seal date: 2026-08-18 (machine local, Asia/Shanghai)
Seal basis: `L7_FINAL_AUDIT.json` verdict **PASS** — every acceptance criterion of the
APPROVED/LOCKED Layer 7 Product Contract verified, including all 10 contract E2E
scenarios, the "评分系统不存在" static audit, and "开发 PC 不属于最终运行依赖".

## What is sealed

Layer 7 — Product Output — as implemented in this workspace:

1. **Backend** `backend/l7/` — the L7 Product API (FastAPI) and its engine:
   three-threshold orchestrator (Recompute < UI Change < Notification), deterministic
   five-state renderer, model-call cache, notification gate, episode/pattern projections,
   Q&A / Context / Feedback services, admin export/delete.
2. **Client** `app/` — the Flutter product app (今日 / 历史 / 我的规律 / 我的 + Q&A,
   Context, Evidence, Feedback, Notifications), schema `l7.today/v1` consumer.
3. **Packaging & ops** — `Dockerfile`, `docker-compose.yml`, `DEPLOYMENT.md`,
   `scripts/backup.py`, `scripts/verify_upstream_integrity.py`.
4. **Contract artifacts** — this file, `L7_FINAL_AUDIT.json`, `L7_HANDOFF.md`,
   phase audits `docs/PHASE_C..H_AUDIT.json`.

## Seal guarantees

- **L1–L6 remain SEALED and were not modified.** Verified by
  `scripts/verify_upstream_integrity.py` (PASS): the production L6 state equals the
  Phase-A-documented baseline exactly; L3/L4/L5 were opened read-only; every L6 write
  during development happened on per-test copies or through sealed write semantics only.
- **L7 does not re-reason.** All health judgment comes from L6 outputs; L7 only
  assembles deterministic evidence, applies sealed policies, renders, and routes.
- **No scoring system exists** (no health/recovery/readiness score, no 0–100, no
  traffic lights, no grades) — static audit PASS.
- **Cost discipline holds**: unchanged evidence → 0 model calls; changed evidence →
  exactly 1; MedGemma only on the sealed trigger policy.
- **Semantics are stable**: wording changes only when the sealed judgment signature
  changes; Today is a live state, never a frozen report.

## Breaking the seal

L7 may be reopened only under the same rules as upstream layers:

- a contract change approved by the product owner, or
- a sealed-interface extension from L1–L6 that L7 must adapt to, or
- a defect that violates a contract guarantee above.

Any change after sealing requires: new/updated tests first, a phase audit addendum,
re-running `verify_upstream_integrity.py`, and re-issuing `L7_FINAL_AUDIT.json`.

## Standing constraints for future work

- Upstream dbs read-only (`mode=ro`); L6 writes only via sealed entry points.
- Secrets env-only, never in repo/client/logs.
- Mock adapters for all automated tests; real calls only behind explicit gates with
  recorded budgets.
- AI inference never promoted to user fact; user correction > AI structuring > AI inference.

## 2026-08-24 Q&A Orchestration V2 addendum

The product owner explicitly reopened the Q&A surface to correct semantic routing,
question-specific grounding, and medical-review ordering. The change is limited to the L6
Context vocabulary extension (`HEADACHE`) and the L7 Q&A, persistence, API, and client
status surfaces. It does not change the sealed L1-L5 analytics or Today judgment contract.

The re-sealed Q&A guarantees are:

- DeepSeek V4 Flash Stage A is the semantic scope authority for every non-empty message;
  conversation history is reference-resolution context, never personal-health evidence.
- Exact and aggregate health-data answers are computed deterministically from isolated
  engine source series; no language model performs arithmetic or invents measurements.
- Decision evidence is filtered to the classified question and published through stable
  evidence references before the Stage B candidate is accepted.
- The complete candidate is produced before the deterministic consequence gate and any
  MedGemma review. Unsafe, ungrounded, malformed, rejected, escalated, or unavailable paths
  fail closed through the finalizer.
- Every turn is auditable by stage order and model identity without storing prompts,
  chain-of-thought, credentials, or private model payloads.
- Flutter reports honest staged progress and does not claim that medical review occurred
  when the gate bypasses it.

Re-seal evidence is recorded in `docs/PHE_QNA_ORCHESTRATION_V2_AUDIT.json` after local and
production acceptance.
