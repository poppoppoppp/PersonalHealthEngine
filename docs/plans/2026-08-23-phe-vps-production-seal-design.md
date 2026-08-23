# PHE VPS Production Seal Design

## Scope

Finish the existing Linux VPS deployment without changing any SEALED L1-L7
contract or semantic behavior. Start at the current L4 environment failure,
advance one layer at a time, run the full systemd pipeline only after isolated
layer acceptance, enable the timer only after the full pipeline passes, then
make the repository reproduce the proven production architecture.

## Operating model

Every failure follows one sequence:

`observe -> isolate -> minimal fix -> verify -> continue`

Production data is never reset or rebuilt for diagnosis. Definition registry
checksums remain authoritative. Any file-byte repair must first prove that the
candidate bytes match the sealed registry value. Secrets are inspected only by
presence, ownership, mode, and safe fingerprints; their values are never
printed or copied into Git.

## Acceptance flow

1. Compare systemd's parsed environment with shell sourcing and prove the exact
   L3 database path available to `phe`.
2. Verify all L4 definition hashes and database invariants, then run only L4 in
   a systemd-equivalent environment.
3. Repeat the definition, database, checkpoint, and runtime gates for L5 and L6.
4. Refresh L7 through localhost with the installed token and verify both model
   backends and the generated Today state.
5. Run `phe-daily.service` once and require `Result=success` across the complete
   Xiaomi-to-L7 chain.
6. Enable and start `phe-daily.timer`, then verify its Asia/Shanghai schedule.
7. Audit services, listeners, SQLite databases, checkpoints, secret modes, and
   autonomous operation.
8. Reconcile VPS deployment changes into the local main repository, repair only
   deployment reproducibility defects, test them, audit Git for secrets and
   runtime artifacts, then commit and push when authentication is available.

## Guardrails

- Do not rerun L1-L3 while isolating L4-L6 failures.
- Do not run the legacy `deployment/scripts/provision_medgemma.sh`.
- Keep Ollama on the host and L7 in Docker.
- Keep ports 8707 and 11434 off public interfaces.
- Do not alter sealed definition registry checksums.
- Do not use destructive Git, database, or permission operations.
