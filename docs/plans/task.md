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
| Establish clean baseline in isolated worktree | L7 pytest, Flutter analyze/test | In progress |
| Implement tested mobile transport errors and timeouts | Focused Dart tests red/green | Not started |
| Implement secure production configuration and token migration | Focused Dart tests red/green | Not started |
| Implement explicit error/retry UX across L7 screens | Widget tests red/green | Not started |
| Add Android release permission, hardening, and signing | Analyze, tests, apksigner verification | Not started |
| Add reproducible Nginx, Certbot, and firewall deployment | Static deployment tests and shell validation | Not started |
| Deploy VPS gateway and verify public production APIs | SSH/cloud authentication required after local work | Blocked external auth |
| Build and validate final production APK | Release build and Android/API equivalent smoke | Not started |
| Full regression, secret audit, commit, push, and final acceptance | Fresh acceptance evidence | Not started |
