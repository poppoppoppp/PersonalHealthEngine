# L7 Deployment Guide (Cloud-ready packaging)

Goal: the product must work **without the development PC being powered on**. Backend,
data and models live on a host the user controls (a small VPS is enough).

## 1. What ships where

| Piece | Location in production |
|---|---|
| Flutter app | user's phone (Android first; Web build also available) |
| L7 Product API | VPS, Docker container `l7-backend`, port 8707 behind TLS |
| Sealed L1–L6 dbs + L6 scripts/definitions | VPS, mounted **read-only** into the container |
| DeepSeek reasoning | DeepSeek API (key only in server env, never in the app) |
| MedGemma medical reviewer | Ollama on the same VPS (compose service) or any remote Ollama endpoint |

Docker is NOT installed on the development PC (discovered in Phase A); the packaging
artifacts (`Dockerfile`, `docker-compose.yml`) are validated by the packaging smoke tests
(prod-like env boot, prod token enforcement, backup/restore round-trip) and must get one
`docker compose up -d --build` on the VPS as the final confirmation.

## 2. VPS setup (once)

```bash
# 1) Docker + compose (any recent Docker Engine)
curl -fsSL https://get.docker.com | sh

# 2) Copy this repo (or just: Dockerfile, docker-compose.yml, backend/) to the VPS.

# 3) Place the sealed data exactly as compose expects:
mkdir -p sealed/l3 sealed/l4 sealed/l5 sealed/l6
# copy each layer's .sqlite3 into its folder
# copy PersonalHealthEngine-L6/scripts -> sealed/l6/scripts
# copy PersonalHealthEngine-L6/definitions -> sealed/l6/definitions
chmod -R a-r+w sealed || true   # keep them read-only for the container user

# 4) Secrets — never committed, never shipped to the phone:
cat > .env <<'EOF'
L7_ENV=prod
L7_API_TOKEN=<openssl rand -hex 32>
DEEPSEEK_API_KEY=<your key>
L7_REASONING_ADAPTER=deepseek
L7_MEDICAL_ADAPTER=medgemma
L7_MEDGEMMA_ENDPOINT=http://medgemma:11434
EOF

# 5) Build & start
docker compose up -d --build
docker compose exec medgemma ollama pull medgemma   # one-time model pull

# 6) TLS in front (Caddy example)
# caddy reverse-proxy --from health.example.com --to 127.0.0.1:8707
```

In the app's 我的 tab set the server address to `https://health.example.com` and the
access token to `L7_API_TOKEN`.

## 3. Data freshness without the dev PC

- The sealed upstream collectors (Mi Fitness export / L2 ingestion / L3–L6 pipelines) run
  wherever the owner keeps them. When they are not running, the product **degrades
  gracefully**: Today keeps serving the latest judgment with its "基于截至 YYYY-MM-DD 的
  全部已知信息" stamp — there is no "report not ready" failure mode.
- If you later migrate the Xiaomi collector to the VPS, keep the same read-only contract:
  L7 opens upstream dbs `mode=ro`; only sealed L6 entry points ever write L6.

## 4. Backups & retention

```bash
# nightly (cron): online-consistent VACUUM INTO of L3–L7, keeps 14 snapshots
python backend/scripts/backup.py --dir /data/backups --keep 14
```
Restore = stop the container, copy a snapshot back over the live file, start. Round-trip
is covered by `tests/test_packaging.py`.

## 5. Per-user export / delete (data portability)

```python
from l7.config import Config
from l7.admin.export_delete import export_user, delete_user
export_user(Config(), "owner")   # JSON: all L7 rows + L6 snapshot, no secrets
delete_user(Config(), "owner")   # removes L7 rows; upstream deletion stays a
                                 # sealed-layer-owner action (documented in result)
```

## 6. Secret-handling review (contract requirement)

- `DEEPSEEK_API_KEY`, `L7_API_TOKEN` exist only in server-side environment / `.env`.
- The app holds only the user's own access token (bearer), configurable in 我的.
- No key material exists in the Flutter code, the repo, or any audit log; QA/feedback
  logs store hashes/ids and user text, never credentials.
- `L7_ENV=prod` refuses to boot without `L7_API_TOKEN` (test_packaging enforced).

## 7. Cost controls that survive deployment

- Unchanged evidence → 0 model calls (recompute threshold).
- Changed evidence bundle → exactly 1 reasoning call (bundle-hash gate + provenance cache).
- MedGemma only on the sealed trigger policy (symptom/safety questions or symptom context).
- Notification gate is deterministic; it never calls a model.
