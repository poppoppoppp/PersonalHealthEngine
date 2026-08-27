| Task | Verification | Status |
|---|---|---|
| Diagnose L4 environment boundary | Exact systemd/shell path difference and DB access evidence | Complete |
| Run and accept L4 | L4 runtime audit and production pipeline PASS | Complete |
| Run and accept L5 | L5 runtime audit and production pipeline PASS | Complete |
| Run and accept L6 | L6 runtime audit and production pipeline PASS | Complete |
| Refresh and accept L7 | HTTP refresh, Today state, MedGemma and DeepSeek PASS | Complete |
| Run complete daily service | `Result=success` with L1-L7 evidence | Complete |
| Enable daily timer | Active, enabled, Asia/Shanghai next trigger | Complete |
| Audit production | Services, listeners, DBs, checkpoints, secret modes PASS | Complete |
| Reconcile deployment code | Local repo reproduces proven production architecture | Complete |
| Audit, commit, and push Git | Secret-clean commit pushed or auth-only blocker | Complete |
| Audit local Flutter, L7, deployment, Git, and public ports | Root cause and external port evidence recorded | Complete |
| Define mainland-China direct HTTPS architecture | Design document recorded | Complete |
| Establish clean baseline in isolated worktree | L7 pytest, Flutter analyze/test | Complete |
| Implement tested mobile transport errors and timeouts | Focused Dart tests red/green | Complete |
| Implement secure production configuration and token migration | Focused Dart tests red/green | Complete |
| Implement explicit error/retry UX across L7 screens | 26 Flutter tests cover primary and secondary network screens | Complete |
| Add Android release permission, hardening, and signing | Signed release APK verified with `apksigner`; manifest audited | Complete |
| Add reproducible Nginx, Certbot, and firewall deployment | 5 static tests, Bash syntax, Python compile | Complete |
| Deploy VPS gateway and verify public production APIs | ECS page is logged in but browser control cannot read it; no SSH/CLI credential exists | Blocked external auth |
| Build and validate final production APK | Release APK built; signature, package, permission, backup flags verified | Complete |
| Full regression, secret audit, commit, push, and final acceptance | 171 passed, 2 skipped, 3 subtests; Git/production gates pending | In progress |
| Audit original product contract and live production behavior | Formal L6/L7 derivatives, source trace, and authenticated production payload evidence | Complete |
| Define contract-conformance repair architecture | Design preserves E/state semantics and selects L7 product/provenance boundary | Complete |
| Lock backend presentation defects with red tests | 8 focused failures reproduce labels, provenance, time, Q&A, language, and protocol drift | Complete |
| Implement canonical labels and exact evidence provenance | 42 focused/related tests green; exact L5→L3/L4 IDs and dated values verified | Complete |
| Enforce Chinese model/Q&A output and adapter protocol | 15 focused tests green; zh-CN daily/Q&A and MedGemma protocol verified | Complete |
| Repair legacy Today presentation and timestamp semantics | 103 L7 tests green; stable refresh and append-only presentation repair verified | Complete |
| Remove raw implementation values from Flutter | 31 Flutter tests green; analyzer 0 errors/0 warnings and 17 baseline infos | Complete |
| Run full local contract acceptance and audit | 182 Python + 31 Flutter tests; A-E scan PASS; audit artifact written | Complete |
| Deploy and accept production backend | VPS backend, authenticated Today/evidence, trusted HTTPS, protected ports PASS | Complete |
| Build and verify final signed APK | V2 signature/package/permission/backup/HTTPS endpoint PASS; SHA-256 recorded | Complete |
| Secret audit, integrate, commit, and push | Secret scan PASS; integrated on main and pushed | Complete |
| Cut over all DeepSeek production paths to V4 Flash non-thinking | Repo defaults, VPS effective env, real Today/Q&A/Context calls, zero Pro calls | Complete |
| Specify shared DeepSeek Flash transport contract | Red tests prove old Pro/effort behavior and audit gaps | Complete |
| Implement fail-closed Flash non-thinking transport | 7 L6 adapter tests pass | Complete |
| Route L7 operations through shared transport | 19 focused L6/L7 tests pass | Complete |
| Align active DeepSeek configuration and static audit | 133 Python tests and model audit PASS | Complete |
| Add isolated real-call Flash acceptance harness | 4 harness tests pass; temporary L6 cleanup verified | Complete |
| Run full DeepSeek cutover regressions and integrate | 196 Python + 31 Flutter tests; analyzer 0 errors/warnings; static audit PASS | Complete |
| Deploy and accept DeepSeek V4 Flash on VPS | Effective env, real Today/Q&A/Context audit, public access, zero Pro calls | Complete |
| Audit PHE Q&A Orchestration V2 baseline and sealed contracts | Keyword authority, review order, persistence, adapters, and UI evidence recorded | Complete |
| Design semantic/evidence/medical/finalizer architecture | Approved production brief mapped to focused L7 orchestration design | Complete |
| Implement PHE Q&A Orchestration V2 with TDD | Semantic, data, decision, review, finalizer, audit, context, and UX tests | Complete |
| Q&A V2 Task 1 — semantic routing and follow-up | 13 focused Q&A tests pass after red/green semantic regressions | Complete |
| Q&A V2 Task 2 — deterministic health-data authority | 4 source-isolated L3 data tests pass | Complete |
| Q&A V2 Task 3 — question-specific evidence and candidate guard | 19 focused tests cover metric filtering, refs, and hallucination veto | Complete |
| Q&A V2 Task 4 — post-candidate medical review and finalizer | 22 focused tests cover order, gate, all statuses, and fail-closed | Complete |
| Q&A V2 Task 5 — audit, context, and persistence integration | 34 L7 + 9 L6 tests cover migration, stage audit, context, and ownership | Complete |
| Q&A V2 Task 6 — Flutter staged processing UX | Red/green widget test covers understanding, evidence, optional safety, success | Complete |
| Q&A V2 Task 7 — regression, production deployment, and release | 216 Python tests, 31 Flutter tests, signed APK, secret audit, five live Q&A cases, and VPS gates pass | Complete |
| Deploy and seal PHE Q&A Orchestration V2 | Deployed c153f04; production audit, rollback backups, staging cleanup, and five live cases verified | Complete |
| Capture PHE Performance V1 baseline and root causes | Idle production reads, Q&A latency, MedGemma starvation, app cache audit, and full test baseline | Complete |
| Design performance and responsiveness architecture | Approved SQLite jobs/read models/exact cache/resource isolation/SWR design | Complete |
| Add sanitized request, stage, model, job, and cache telemetry | Focused privacy/aggregation tests plus production stage timings | Complete |
| Serve bounded versioned read projections | Today/History/Timeline/Context pagination, ETag, query-plan tests | Complete |
| Move Context and Feedback recompute to durable jobs | Write-before-ack, idempotency, worker, and concurrency tests | Complete |
| Optimize Q&A and MedGemma review | Fast paths, compact bundle, exact cache; real 2-vCPU review remains 563,888 ms | Hardware limit |
| Add Flutter SWR caches and request coalescing | Cached first paint, prefetch, pagination, stale-version and corruption tests | Complete |
| Isolate production inference resources | Normal read p95 7.77–10.96 ms during/after real medical work | Complete |
| Deploy, reboot, benchmark, build APK, and seal V1 | Production/APK/reboot pass; physical launch unmeasured and raw MedGemma target fails | In progress |
| Defer long medical Q&A through the durable worker | 202 persisted ack p95 81.54 ms, authenticated result polling, fail-closed Flutter UX | Complete |
| Diagnose stale 2026-08-20 production data | L1/L2 PASS; L3 heart-rate definition checksum drift identified | Complete |
| Correct evidence freshness semantics | Red/green test uses current Shanghai date rather than stale analysis date | In progress |
| Add fail-closed definition reconciliation preflight | systemd ordering test and deployment suite pass | Pending |
| Recover and verify 2026-08-27 daily pipeline | L1-L7 PASS, L5 date advances, public Today freshness verified | Pending |

Verification snapshot (2026-08-24):

- Python: `216 passed, 2 skipped, 982 warnings, 3 subtests passed`.
- Flutter: `31 passed`; analyzer has 0 errors, 0 warnings, and 17 pre-existing info-level lints.
- APK: V2 signature valid, signer `CN=Personal Health Engine, O=Private, C=CN`, package `com.personalhealthengine.phe_app`, INTERNET present, backup disabled.
- Production: commit `c153f04` healthy; five required Q&A cases pass; protected 8707/11434 closed; daily timer active and enabled.

Performance V1 verification snapshot (2026-08-26):

- Backend: `159 passed`; deployment: `15 passed, 2 skipped`; focused Q&A/safety/jobs: `50 passed`.
- Flutter: `44 passed`; analyzer has 0 errors, 0 warnings, and 17 pre-existing info-level lints.
- Production: version `0.2.0`, runtime commit `e473e15`, reboot recovery PASS, backend healthy, worker running, 8707/11434 closed.
- Real physical-activity Q&A: persisted 202 ack `44.45 ms`; public deduplicated ack p95 `81.54 ms`; grounded MedGemma-reviewed result succeeded once in `563887.99 ms`.
- APK: V2 signature valid; SHA-256 `02BB2552858AF983C8B332778B1B9E5957FC854C2993DAEDB806F5C9FC648B54`.
- Final audit: `PHE_PERFORMANCE_AUDIT.json`; V1 remains unsealed because physical launch timing is unmeasured and the 2-vCPU MedGemma review misses the 60-second target.
