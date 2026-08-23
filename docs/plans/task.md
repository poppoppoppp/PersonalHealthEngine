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

Verification snapshot (2026-08-24):

- Python: `171 passed, 2 skipped, 3 subtests passed`.
- Flutter: `26 passed`; analyzer has 0 errors, 0 warnings, and 17 pre-existing info-level lints.
- APK: V2 signature valid, signer `CN=Personal Health Engine, O=Private, C=CN`, package `com.personalhealthengine.phe_app`, INTERNET present, backup disabled.
- Public ports before gateway deployment: 80/443 closed; protected 8707/11434 closed.
