from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_android_release_has_network_permission_and_no_backup():
    manifest = read(
        "PersonalHealthEngine-L7/app/android/app/src/main/AndroidManifest.xml"
    )
    assert "android.permission.INTERNET" in manifest
    assert 'android:allowBackup="false"' in manifest
    assert "usesCleartextTraffic=\"true\"" not in manifest


def test_android_release_never_falls_back_to_debug_signing():
    gradle = read("PersonalHealthEngine-L7/app/android/app/build.gradle.kts")
    assert "key.properties" in gradle
    assert 'signingConfigs.getByName("debug")' not in gradle
    assert "storeFile" in gradle


def test_release_builder_supports_windows_powershell_5_and_cleans_secrets():
    script = read("deployment/scripts/build_android_release.ps1")
    assert "utf8NoBOM" not in script
    assert "UTF8Encoding" in script
    assert "Remove-Item -LiteralPath $keyProperties" in script
    assert "Remove-Item -LiteralPath $definesFile" in script


def test_gateway_only_proxies_https_to_local_l7():
    http = read("deployment/nginx/phe-mobile-http.conf")
    https = read("deployment/nginx/phe-mobile-https.conf")
    assert "listen 80" in http
    assert "/.well-known/acme-challenge/" in http
    assert "return 308 https://$host$request_uri" in http
    assert "listen 443 ssl" in https
    assert "ssl http2 default_server" in https
    assert "http2 on;" not in https
    assert "proxy_pass http://127.0.0.1:8707" in https
    assert "proxy_connect_timeout 10s" in https
    assert "proxy_read_timeout 900s" in https
    assert "listen 8707" not in http + https
    assert "listen 11434" not in http + https


def test_ip_certificate_and_automatic_renewal_are_configured():
    https = read("deployment/nginx/phe-mobile-https.conf")
    installer = read("deployment/scripts/install_mobile_gateway.sh")
    service = read("deployment/systemd/phe-certbot-renew.service")
    timer = read("deployment/systemd/phe-certbot-renew.timer")
    assert "/etc/letsencrypt/live/47.111.229.39/fullchain.pem" in https
    assert "--preferred-profile shortlived" in installer
    assert 'PUBLIC_IP="47.111.229.39"' in installer
    assert '--ip-address "${PUBLIC_IP}"' in installer
    assert "certbot renew" in service
    assert "nginx -s reload" in service
    assert "OnCalendar=*-*-* 00/12:17:00" in timer


def test_mobile_gateway_installer_keeps_internal_ports_private():
    installer = read("deployment/scripts/install_mobile_gateway.sh")
    assert "0.0.0.0:8707" not in installer
    assert "0.0.0.0:11434" not in installer
    assert "nginx -t" in installer
    assert "systemctl enable --now nginx" in installer


def test_production_runs_exactly_one_private_durable_worker():
    compose = read("deployment/docker/docker-compose.production.yml")
    service = read("deployment/systemd/phe-l7-worker.service")
    assert "l7-worker:" in compose
    assert "python\n      - -m\n      - l7.worker" in compose
    assert compose.count("l7-worker:") == 1
    worker_block = compose.split("l7-worker:", 1)[1]
    assert "restart: unless-stopped" in worker_block
    assert "ports:" not in worker_block
    assert "docker compose" in service
    assert "l7-worker" in service
    assert "Restart=on-failure" in service


def test_medical_inference_is_bounded_and_lower_priority():
    ollama = read("deployment/systemd/ollama.service")
    compose = read("deployment/docker/docker-compose.production.yml")
    assert "Nice=10" in ollama
    assert "IOSchedulingClass=idle" in ollama
    assert 'Environment="OLLAMA_NUM_PARALLEL=1"' in ollama
    assert 'Environment="OLLAMA_MAX_LOADED_MODELS=1"' in ollama
    assert 'Environment="OLLAMA_KEEP_ALIVE=30m"' in ollama
    assert 'Environment="OLLAMA_CONTEXT_LENGTH=2048"' in ollama
    assert "MEDGEMMA_NUM_THREAD: \"1\"" in compose
    assert "MEDGEMMA_NUM_CTX: \"2048\"" in compose
    assert "MEDGEMMA_NUM_PREDICT:" in compose
    assert "MEDGEMMA_KEEP_ALIVE: 30m" in compose


def test_benchmark_tools_never_log_tokens_or_prompts():
    med = read("deployment/scripts/benchmark_medgemma.py")
    concurrent = read("deployment/scripts/benchmark_concurrency.py")
    combined = med + concurrent
    assert "L7_API_TOKEN" in combined
    assert "Authorization" in combined
    assert "print(prompt" not in combined
    assert "print(token" not in combined
    assert "print(json.dumps(payload" not in combined
