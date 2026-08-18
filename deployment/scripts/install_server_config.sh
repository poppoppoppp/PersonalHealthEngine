#!/usr/bin/env bash
set -euo pipefail

# Install Personal Health Engine host configuration templates.
#
# Safe behavior:
# - never overwrite existing production configuration
# - never write real secrets
# - install files readable only by root + phe group

CODE_ROOT="/opt/phe"
CONFIG_ROOT="/etc/phe"

PATH_SOURCE="${CODE_ROOT}/deployment/config/production-paths.conf.example"
RUNTIME_SOURCE="${CODE_ROOT}/deployment/config/runtime.env.example"

PATH_TARGET="${CONFIG_ROOT}/production-paths.conf"
RUNTIME_TARGET="${CONFIG_ROOT}/runtime.env"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: run this script as root"
    exit 1
fi

if ! id phe >/dev/null 2>&1; then
    echo "ERROR: phe user does not exist"
    echo "Run bootstrap_server.sh first."
    exit 1
fi

if [[ ! -f "${PATH_SOURCE}" ]]; then
    echo "ERROR: missing ${PATH_SOURCE}"
    exit 1
fi

if [[ ! -f "${RUNTIME_SOURCE}" ]]; then
    echo "ERROR: missing ${RUNTIME_SOURCE}"
    exit 1
fi

install -d \
    -m 0750 \
    -o root \
    -g phe \
    "${CONFIG_ROOT}"

echo "========== PHE CONFIG INSTALL =========="

if [[ ! -e "${PATH_TARGET}" ]]; then
    install \
        -m 0640 \
        -o root \
        -g phe \
        "${PATH_SOURCE}" \
        "${PATH_TARGET}"

    echo "CREATED: ${PATH_TARGET}"
else
    echo "PRESERVED: ${PATH_TARGET}"
fi

if [[ ! -e "${RUNTIME_TARGET}" ]]; then
    install \
        -m 0640 \
        -o root \
        -g phe \
        "${RUNTIME_SOURCE}" \
        "${RUNTIME_TARGET}"

    echo "CREATED: ${RUNTIME_TARGET}"
else
    echo "PRESERVED: ${RUNTIME_TARGET}"
fi

# Always restore expected ownership / permissions.
chown root:phe \
    "${PATH_TARGET}" \
    "${RUNTIME_TARGET}"

chmod 0640 \
    "${PATH_TARGET}" \
    "${RUNTIME_TARGET}"

echo
echo "production_paths = ${PATH_TARGET}"
echo "runtime_env      = ${RUNTIME_TARGET}"
echo

if grep -q "__SET_ON_SERVER__" "${RUNTIME_TARGET}"; then
    echo "RUNTIME CONFIG STATUS = PROVISIONING REQUIRED"
    echo
    echo "Before production start, replace:"
    echo "  L7_API_TOKEN=__SET_ON_SERVER__"
    echo "  DEEPSEEK_API_KEY=__SET_ON_SERVER__"
else
    echo "RUNTIME CONFIG STATUS = PROVISIONED"
fi

echo
echo "PHE CONFIG INSTALL = PASS"
