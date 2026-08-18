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
