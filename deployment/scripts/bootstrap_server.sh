#!/usr/bin/env bash
set -euo pipefail

# Personal Health Engine production host bootstrap.
# Target: Linux VPS
# Run once as root after the repository is placed at /opt/phe.

PHE_USER="phe"
PHE_GROUP="phe"
PHE_UID="10001"
PHE_GID="10001"

CODE_ROOT="/opt/phe"
DATA_ROOT="/srv/phe"
CONFIG_ROOT="/etc/phe"

if [[ "${EUID}" -ne 0 ]]; then
    echo "ERROR: run this script as root"
    exit 1
fi

echo "========== PHE SERVER INITIALIZATION =========="

# ------------------------------------------------------------
# Service account
# ------------------------------------------------------------

if ! getent group "${PHE_GROUP}" >/dev/null 2>&1; then
    groupadd \
        --gid "${PHE_GID}" \
        "${PHE_GROUP}"
fi

if ! id "${PHE_USER}" >/dev/null 2>&1; then
    useradd \
        --uid "${PHE_UID}" \
        --gid "${PHE_GID}" \
        --system \
        --create-home \
        --shell /usr/sbin/nologin \
        "${PHE_USER}"
fi

actual_uid="$(id -u "${PHE_USER}")"
actual_gid="$(id -g "${PHE_USER}")"

if [[ "${actual_uid}" != "${PHE_UID}" ]]; then
    echo "ERROR: phe UID is ${actual_uid}, expected ${PHE_UID}"
    exit 1
fi

if [[ "${actual_gid}" != "${PHE_GID}" ]]; then
    echo "ERROR: phe GID is ${actual_gid}, expected ${PHE_GID}"
    exit 1
fi

# ------------------------------------------------------------
# Persistent directories
# ------------------------------------------------------------

install -d -m 0750 -o phe -g phe \
    "${DATA_ROOT}" \
    "${DATA_ROOT}/l1" \
    "${DATA_ROOT}/l1/captures" \
    "${DATA_ROOT}/l1/state" \
    "${DATA_ROOT}/l2" \
    "${DATA_ROOT}/l2/db" \
    "${DATA_ROOT}/l2/archive" \
    "${DATA_ROOT}/l2/backups" \
    "${DATA_ROOT}/l3" \
    "${DATA_ROOT}/l3/db" \
    "${DATA_ROOT}/l4" \
    "${DATA_ROOT}/l4/db" \
    "${DATA_ROOT}/l5" \
    "${DATA_ROOT}/l5/db" \
    "${DATA_ROOT}/l6" \
    "${DATA_ROOT}/l6/db" \
    "${DATA_ROOT}/l7" \
    "${DATA_ROOT}/l7/db" \
    "${DATA_ROOT}/runtime" \
    "${DATA_ROOT}/logs" \
    "${DATA_ROOT}/backups" \
    "${DATA_ROOT}/ollama"

# ------------------------------------------------------------
# Secret/config directory
# ------------------------------------------------------------

install -d -m 0750 -o root -g phe \
    "${CONFIG_ROOT}" \
    "${CONFIG_ROOT}/secrets"

# Create empty Xiaomi secret files only if absent.
# Never overwrite existing credentials.

if [[ ! -e "${CONFIG_ROOT}/secrets/xiaomi_user_id" ]]; then
    install -m 0640 -o root -g phe \
        /dev/null \
        "${CONFIG_ROOT}/secrets/xiaomi_user_id"
fi

if [[ ! -e "${CONFIG_ROOT}/secrets/xiaomi_pass_token" ]]; then
    install -m 0640 -o root -g phe \
        /dev/null \
        "${CONFIG_ROOT}/secrets/xiaomi_pass_token"
fi

# ------------------------------------------------------------
# Code-root sanity
# ------------------------------------------------------------

if [[ ! -d "${CODE_ROOT}" ]]; then
    echo "WARNING: ${CODE_ROOT} does not exist yet."
    echo "Clone PersonalHealthEngine there before production start."
fi

# ------------------------------------------------------------
# Final permission audit
# ------------------------------------------------------------

test -r "${CONFIG_ROOT}/secrets/xiaomi_user_id"
test -r "${CONFIG_ROOT}/secrets/xiaomi_pass_token"

test "$(stat -c '%U:%G' "${DATA_ROOT}/runtime")" = "phe:phe"
test "$(stat -c '%U:%G' "${DATA_ROOT}/l6/db")" = "phe:phe"
test "$(stat -c '%U:%G' "${DATA_ROOT}/l7/db")" = "phe:phe"

echo
echo "PHE SERVER INITIALIZATION = PASS"
echo "phe_uid = $(id -u phe)"
echo "phe_gid = $(id -g phe)"
echo "code_root = ${CODE_ROOT}"
echo "data_root = ${DATA_ROOT}"
echo "config_root = ${CONFIG_ROOT}"
echo
echo "Secrets remain EMPTY until explicitly provisioned."
