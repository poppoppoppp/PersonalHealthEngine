# Personal Health Engine — L7 Product Backend
# Cloud-ready image: the app talks to this service; model credentials stay server-side.
#
# IMPORTANT: the sealed L1–L6 code+data are mounted read-only at runtime (see
# docker-compose.yml). This image contains ONLY L7.
FROM python:3.14-slim

WORKDIR /srv/l7

# curl is handy for healthchecks; keep the image small otherwise.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /srv/l7/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend /srv/l7/backend
# Sealed L6 code is import-only; ship a copy location that the compose volume overrides.
ENV PYTHONPATH=/srv/l7/backend

# Non-root runtime user.
RUN useradd -m -u 10001 l7 && mkdir -p /data/l7 && chown -R l7:l7 /data/l7 /srv/l7
USER l7

ENV L7_DB=/data/l7/l7_product.sqlite3
EXPOSE 8707

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s \
  CMD curl -fsS http://127.0.0.1:8707/health || exit 1

WORKDIR /srv/l7/backend
CMD ["python", "-m", "uvicorn", "l7.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8707"]
