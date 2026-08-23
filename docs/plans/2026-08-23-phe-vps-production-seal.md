# PHE VPS Production Seal Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Seal the existing Personal Health Engine VPS deployment and make the main repository reproduce the accepted production architecture.

**Architecture:** Diagnose and accept each downstream layer independently before one full systemd run. Treat the production registry and deployed service configuration as evidence, make only root-cause fixes, and reconcile proven VPS changes back to the local repository after production passes.

**Tech Stack:** PowerShell, OpenSSH, Bash, systemd, Python 3.13, SQLite, Docker Compose, host Ollama, MedGemma, DeepSeek, Git.

---

### Task 1: Establish the L4 root cause

**Files:**
- Inspect: `/etc/systemd/system/phe-daily.service`
- Inspect: `/etc/phe/production-paths.conf`
- Inspect: `/etc/phe/runtime.env`
- Inspect: `/srv/phe/l3/db/personal_health_features.sqlite3`
- Inspect: `/srv/phe/l4/db/personal_health_baselines.sqlite3`

1. Capture `systemctl cat` and `systemctl show` for the service's user, group,
   working directory, environment files, commands, and last result.
2. Inspect configuration bytes and EOLs without printing secret values.
3. Compare systemd's parsed `PHE_L3_DB` with shell-sourced bytes.
4. As `phe`, prove path traversal, file readability, and read-only SQLite open.
5. Hash all four L4 definitions and compare them with registry rows.
6. Run L4 database integrity, foreign-key, schema, checkpoint, and latest-run
   queries read-only.
7. Record a single root-cause hypothesis supported by the collected evidence.

Expected: the exact boundary that turns a valid systemd path into the failing
manual invocation is identified without changing production state.

### Task 2: Accept L4

**Files:**
- Execute: `/opt/phe/deployment/scripts/run_l4_pipeline.py`
- Modify only if root cause requires it: deployed configuration or deployment tooling

1. Create a minimal reproduction for the confirmed defect.
2. Verify the reproduction fails for the expected reason.
3. Apply one minimal fix at the source.
4. Run only L4 with systemd-equivalent environment parsing.
5. Require `L4 RUNTIME AUDIT = PASS` and `L4 PRODUCTION PIPELINE = PASS`.
6. Repeat L4 integrity, foreign-key, schema, checkpoint, and runtime status
   checks after the run.

### Task 3: Accept L5 and L6

**Files:**
- Execute: `/opt/phe/deployment/scripts/run_l5_pipeline.py`
- Inspect/execute: the deployed L6 production entry point

1. Establish definition-registry hash equality before each layer run.
2. Run only L5; require runtime audit and production pipeline PASS.
3. Verify L5 integrity, foreign keys, schema, checkpoints, and latest status.
4. Identify the sealed L6 production wrapper and its DeepSeek/MedGemma runtime
   configuration without exposing credentials.
5. Run only L6; require its runtime audit and production pipeline PASS.
6. Verify L6 integrity, foreign keys, checkpoints, latest status, and both model
   dependencies.

### Task 4: Accept L7 refresh

**Files:**
- Execute: `/opt/phe/deployment/scripts/refresh_l7_after_pipeline.py`
- Inspect: `/opt/phe/deployment/docker/docker-compose.production.yml`

1. Verify the L7 container is healthy and bound to `127.0.0.1:8707`.
2. Verify host Ollama and Docker-to-host Ollama connectivity.
3. Run the refresh wrapper with systemd-equivalent environment parsing.
4. Require HTTP success and inspect the refreshed Today state without printing
   tokens or sensitive health payloads.
5. Verify MedGemma and DeepSeek paths used by the accepted L7/L6 flow.

### Task 5: Run the complete production service

**Files:**
- Execute: `/etc/systemd/system/phe-daily.service`

1. Start `phe-daily.service` once.
2. Follow the journal until completion without interrupting a healthy run.
3. Require L1-L7 PASS evidence and `Result=success`.
4. Verify the new Xiaomi observation and downstream checkpoints are consistent.

### Task 6: Enable the production timer

**Files:**
- Inspect/modify if required: `/etc/systemd/system/phe-daily.timer`

1. Verify `OnCalendar=*-*-* 10:30:00 Asia/Shanghai` and `Persistent=true`.
2. Enable and start the timer.
3. Require active and enabled states.
4. Record the actual next trigger and verify timezone interpretation.

### Task 7: Run final production audit

**Files:**
- Inspect: production services, listeners, databases, checkpoints, and secret metadata

1. Verify `phe-daily.service`, `phe-daily.timer`, `ollama.service`, and the L7
   container.
2. Verify neither 8707 nor 11434 listens on a public interface and that no new
   public listener was introduced.
3. Run fresh SQLite integrity and foreign-key checks for L2-L6.
4. Verify checkpoints and latest runtime statuses are current.
5. Verify secret file ownership and permissions without reading values.
6. Prove the VPS no longer depends on the Windows machine for acquisition or
   daily execution.

### Task 8: Reconcile deployment code

**Files:**
- Modify as proven necessary: `PersonalHealthEngine-L7/Dockerfile`
- Modify as proven necessary: `deployment/docker/docker-compose.production.yml`
- Modify as proven necessary: `deployment/config/runtime.env.example`
- Modify as proven necessary: `deployment/scripts/provision_medgemma.sh`
- Modify as proven necessary: `deployment/scripts/create_vps_migration_bundle.py`
- Modify as proven necessary: `deployment/scripts/restore_vps_state.py`
- Modify as proven necessary: `deployment/README.md`
- Test: relevant deployment and layer test suites

1. Compare local Git, VPS Git diff, and deployed file bytes.
2. Classify each difference as required production architecture, temporary
   diagnostic artifact, runtime state, backup, or secret.
3. For each reproducibility bug, write a failing local test or deterministic
   fixture first and verify the expected failure.
4. Implement only the proven deployment fix and verify the focused test passes.
5. Run the relevant full test suites and deployment syntax/config checks.

### Task 9: Git audit, commit, and push

**Files:**
- Inspect: all tracked and untracked changes

1. Review `git status`, staged/unstaged diffs, ignored files, and object-boundary
   secret scans.
2. Exclude tokens, credentials, databases, models, logs, backups, and diagnostics.
3. Re-run all acceptance checks affected by the final diff.
4. Commit the verified deployment closure.
5. Push to `origin` if authentication is already available; otherwise stop only
   for GitHub authentication.
