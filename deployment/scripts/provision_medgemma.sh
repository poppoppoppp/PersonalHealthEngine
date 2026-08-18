#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="medgemma1.5"
EXPECTED_SHA256="a051c2bd4ab8d5b7f4df8eec344f2fdd603efb2d098da799dc16c95e9e8bc838"

CODE_ROOT="/opt/phe"
MODEL_IMPORT_DIR="/srv/phe/model-import"

GGUF="${MODEL_IMPORT_DIR}/medgemma1.5.gguf"
MODELFILE_SOURCE="${CODE_ROOT}/deployment/models/medgemma1.5.Modelfile"
MODELFILE_TARGET="${MODEL_IMPORT_DIR}/medgemma1.5.Modelfile"

COMPOSE_FILE="${CODE_ROOT}/deployment/docker/docker-compose.production.yml"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: run this script as root"
    exit 1
fi

echo "========== MEDGEMMA PROVISIONING =========="

for command in docker sha256sum awk grep; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "ERROR: required command missing: ${command}"
        exit 1
    fi
done

if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: Docker Compose plugin is unavailable"
    exit 1
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "ERROR: compose file missing: ${COMPOSE_FILE}"
    exit 1
fi

if [[ ! -f "${MODELFILE_SOURCE}" ]]; then
    echo "ERROR: committed Modelfile missing: ${MODELFILE_SOURCE}"
    exit 1
fi

if [[ ! -f "${GGUF}" ]]; then
    echo "ERROR: MedGemma GGUF is missing: ${GGUF}"
    echo "Expected SHA256: ${EXPECTED_SHA256}"
    exit 1
fi

actual_sha="$(
    sha256sum "${GGUF}" |
    awk '{print $1}'
)"

echo "GGUF SHA256 = ${actual_sha}"

if [[ "${actual_sha}" != "${EXPECTED_SHA256}" ]]; then
    echo "ERROR: GGUF SHA256 mismatch"
    exit 1
fi

echo "GGUF SHA256 CHECK = PASS"

install \
    -m 0640 \
    -o phe \
    -g phe \
    "${MODELFILE_SOURCE}" \
    "${MODELFILE_TARGET}"

chown phe:phe "${GGUF}"
chmod 0640 "${GGUF}"

echo "Starting Ollama..."

docker compose \
    -f "${COMPOSE_FILE}" \
    up -d medgemma

ready=0

for _ in $(seq 1 60); do
    if docker compose \
        -f "${COMPOSE_FILE}" \
        exec -T medgemma \
        ollama list \
        >/dev/null 2>&1
    then
        ready=1
        break
    fi

    sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
    echo "ERROR: Ollama did not become ready within 60 seconds"
    exit 1
fi

echo "OLLAMA = READY"

docker compose \
    -f "${COMPOSE_FILE}" \
    exec -T medgemma \
    ollama create "${MODEL_NAME}" \
    -f /model-import/medgemma1.5.Modelfile

docker compose \
    -f "${COMPOSE_FILE}" \
    exec -T medgemma \
    ollama show "${MODEL_NAME}" \
    >/dev/null

if ! docker compose \
    -f "${COMPOSE_FILE}" \
    exec -T medgemma \
    ollama list |
    grep -q '^medgemma1\.5:'
then
    echo "ERROR: medgemma1.5 missing after creation"
    exit 1
fi

echo
docker compose \
    -f "${COMPOSE_FILE}" \
    exec -T medgemma \
    ollama list

echo
echo "MEDGEMMA PROVISIONING = PASS"
echo "model = ${MODEL_NAME}"
echo "gguf_sha256 = ${actual_sha}"
