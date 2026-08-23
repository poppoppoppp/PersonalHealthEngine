#!/usr/bin/env bash
set -euo pipefail

CODE_ROOT="${PHE_CODE_ROOT:-/opt/phe}"
PUBLIC_IP="47.111.229.39"
CERTBOT_ROOT="/opt/phe-gateway-certbot"
ACME_ROOT="/var/www/phe-acme"

if [[ "${EUID}" -ne 0 ]]; then
    echo "install_mobile_gateway.sh must run as root" >&2
    exit 2
fi

if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y nginx python3-venv
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y nginx python3 python3-pip
elif command -v yum >/dev/null 2>&1; then
    yum install -y nginx python3 python3-pip
else
    echo "Unsupported package manager" >&2
    exit 3
fi

python3 -m venv "${CERTBOT_ROOT}"
"${CERTBOT_ROOT}/bin/pip" install --upgrade \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    "certbot>=5.4"

install -d -m 0755 "${ACME_ROOT}/.well-known/acme-challenge"
install -m 0644 "${CODE_ROOT}/deployment/nginx/phe-mobile-http.conf" \
    /etc/nginx/conf.d/phe-mobile-http.conf
rm -f /etc/nginx/conf.d/default.conf /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

if [[ ! -s "/etc/letsencrypt/live/${PUBLIC_IP}/fullchain.pem" ]]; then
    "${CERTBOT_ROOT}/bin/certbot" certonly \
        --non-interactive \
        --agree-tos \
        --register-unsafely-without-email \
        --preferred-profile shortlived \
        --webroot \
        --webroot-path "${ACME_ROOT}" \
        --ip-address "${PUBLIC_IP}"
fi

install -m 0644 "${CODE_ROOT}/deployment/nginx/phe-mobile-https.conf" \
    /etc/nginx/conf.d/phe-mobile-https.conf
install -m 0644 "${CODE_ROOT}/deployment/systemd/phe-certbot-renew.service" \
    /etc/systemd/system/phe-certbot-renew.service
install -m 0644 "${CODE_ROOT}/deployment/systemd/phe-certbot-renew.timer" \
    /etc/systemd/system/phe-certbot-renew.timer
nginx -t
systemctl reload nginx
systemctl daemon-reload
systemctl enable --now phe-certbot-renew.timer

echo "PHE mobile HTTPS gateway installed for ${PUBLIC_IP}"
