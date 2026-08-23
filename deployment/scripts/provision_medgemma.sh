#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="medgemma1.5"
EXPECTED_SHA256="a051c2bd4ab8d5b7f4df8eec344f2fdd603efb2d098da799dc16c95e9e8bc838"

CODE_ROOT="/opt/phe"
DATA_ROOT="/srv/phe"
MODEL_IMPORT_DIR="${DATA_ROOT}/model-import"
OLLAMA_HOME="${DATA_ROOT}/ollama"

GGUF="${MODEL_IMPORT_DIR}/medgemma1.5.gguf"
MODELFILE_SOURCE="${CODE_ROOT}/deployment/models/medgemma1.5.Modelfile"
MODELFILE_TARGET="${MODEL_IMPORT_DIR}/medgemma1.5.Modelfile"
FIREWALL_SOURCE="${CODE_ROOT}/deployment/systemd/phe-ollama-firewall.service"
OLLAMA_SERVICE_SOURCE="${CODE_ROOT}/deployment/systemd/ollama.service"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: run this script as root"
    exit 1
fi

echo "========== HOST MEDGEMMA PROVISIONING =========="

for command in ollama sha256sum awk grep systemctl curl; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "ERROR: required host command missing: ${command}"
        exit 1
    fi
done

if [[ ! -x /usr/local/bin/ollama ]]; then
    echo "ERROR: host Ollama must be installed at /usr/local/bin/ollama"
    exit 1
fi

if [[ ! -f "${MODELFILE_SOURCE}" ]]; then
    echo "ERROR: committed Modelfile missing: ${MODELFILE_SOURCE}"
    exit 1
fi

if [[ ! -f "${FIREWALL_SOURCE}" ]]; then
    echo "ERROR: Ollama firewall unit missing: ${FIREWALL_SOURCE}"
    exit 1
fi

if [[ ! -f "${OLLAMA_SERVICE_SOURCE}" ]]; then
    echo "ERROR: Ollama service unit missing: ${OLLAMA_SERVICE_SOURCE}"
    exit 1
fi

if [[ ! -f "${GGUF}" ]]; then
    echo "ERROR: MedGemma GGUF is missing: ${GGUF}"
    echo "Expected SHA256: ${EXPECTED_SHA256}"
    exit 1
fi

actual_sha="$(sha256sum "${GGUF}" | awk '{print $1}')"
echo "GGUF SHA256 = ${actual_sha}"

if [[ "${actual_sha}" != "${EXPECTED_SHA256}" ]]; then
    echo "ERROR: GGUF SHA256 mismatch"
    exit 1
fi

echo "GGUF SHA256 CHECK = PASS"

if ! id ollama >/dev/null 2>&1; then
    useradd --system --home-dir "${OLLAMA_HOME}" --shell /usr/sbin/nologin ollama
fi

install -d -m 0750 -o ollama -g ollama "${OLLAMA_HOME}" "${OLLAMA_HOME}/models"
install -d -m 0750 -o ollama -g ollama "${MODEL_IMPORT_DIR}"
install -m 0640 -o ollama -g ollama "${MODELFILE_SOURCE}" "${MODELFILE_TARGET}"
chown ollama:ollama "${GGUF}"
chmod 0640 "${GGUF}"

install -m 0644 -o root -g root \
    "${FIREWALL_SOURCE}" \
    /etc/systemd/system/phe-ollama-firewall.service
install -m 0644 -o root -g root \
    "${OLLAMA_SERVICE_SOURCE}" \
    /etc/systemd/system/ollama.service

systemctl daemon-reload
systemctl enable --now phe-ollama-firewall.service
systemctl enable --now ollama.service

ready=0
for _ in $(seq 1 60); do
    if curl --fail --silent --show-error \
        http://127.0.0.1:11434/api/tags \
        >/dev/null 2>&1
    then
        ready=1
        break
    fi
    sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
    echo "ERROR: host Ollama did not become ready within 60 seconds"
    exit 1
fi

echo "HOST OLLAMA = READY"

ollama create "${MODEL_NAME}" -f "${MODELFILE_TARGET}"
ollama show "${MODEL_NAME}" >/dev/null

if ! ollama list | grep -q '^medgemma1\.5:'; then
    echo "ERROR: medgemma1.5 missing after creation"
    exit 1
fi

echo
ollama list

echo
echo "HOST MEDGEMMA PROVISIONING = PASS"
echo "model = ${MODEL_NAME}"
echo "gguf_sha256 = ${actual_sha}"
