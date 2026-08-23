# Personal Health Engine — Production Deployment

This directory contains deployment-only infrastructure.

## Frozen boundary

The sealed L1-L7 core implementations are not modified unless a verified
deployment blocker cannot be solved outside the layer.

Deployment responsibilities:

- Linux/VPS runtime paths
- L1-L5 pipeline orchestration
- L6/L7 runtime wiring
- DeepSeek environment configuration
- MedGemma/Ollama service configuration
- concurrency / single-run protection
- runtime health checks
- backups
- systemd scheduling
- Docker composition

Target production chain:

Xiaomi Cloud
-> L1 Collector
-> L2 Raw Store
-> L3 Feature Engineering
-> L4 Personal Baseline
-> L5 Health Analytics
-> L6 AI Reasoning
-> L7 Product API
-> Flutter App

The Windows development PC is not part of the final production runtime.

## Production runtime

- Host systemd runs the Xiaomi-to-L5 daily pipeline as `phe`.
- L7 runs in Docker and is published only on `127.0.0.1:8707`.
- Ollama runs on the host. L7 reaches it through
  `host.docker.internal:11434`; `phe-ollama-firewall.service` rejects
  non-local, non-Docker traffic to that port.
- L6 reasoning is materialized by the L7 orchestrator with DeepSeek and the
  host MedGemma adapter.

## Reproducible installation order

1. Place a clean Git checkout at `/opt/phe` and run `bootstrap_server.sh`.
2. Install host configuration with `install_server_config.sh`, then provision
   the real secret values directly in `/etc/phe`.
3. Restore the signed production-state bundle with `restore_vps_state.py`.
   Restore automatically repairs Windows backslash filenames and CRLF drift
   only when the repaired bytes exactly match each sealed definition registry.
4. Install host Ollama, put the verified GGUF in `/srv/phe/model-import`, and
   run `provision_medgemma.sh` to install its systemd service, firewall, and
   model. The script does not deploy Docker Ollama.
5. Build `docker-compose.production.yml`, install the committed systemd units,
   run `phe-daily.service` once, and enable `phe-daily.timer` only after that
   service succeeds.

Definition JSON is also forced to LF by `.gitattributes`. Neither the restore
tool nor runtime tooling ever updates `definition_registry` checksums.
