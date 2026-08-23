# PHE Mobile Production Access Implementation Plan

> **For Antigravity:** REQUIRED WORKFLOW: Use `.agent/workflows/execute-plan.md` to execute this plan in single-flow mode.

**Goal:** Ship a signed Android APK that connects without user configuration to the mainland-China Alibaba ECS L7 API through publicly trusted HTTPS and always exits loading into success or an explicit retryable error.

**Architecture:** Terminate a Let's Encrypt short-lived IP certificate at Nginx on 443 and proxy only to localhost L7. Inject the existing bearer token at release build time, migrate it into Android secure storage, and keep the public URL separately configurable. Add typed transport failures and shared retry UX without changing any L1-L7 business contract.

**Tech Stack:** Flutter/Dart, package:http, flutter_secure_storage, SharedPreferences, Android Gradle signing, Nginx, Certbot 5.4+, systemd, pytest, PowerShell/OpenSSH.

---

### Task 1: Establish and record the clean baseline

**Files:**
- Modify: `docs/plans/task.md`

**Step 1:** Run `py -3.14 -m pytest PersonalHealthEngine-L7/backend/tests -q` from the repository root.

**Step 2:** Run `flutter analyze` and `flutter test` from `PersonalHealthEngine-L7/app` with Flutter version checks disabled.

**Step 3:** Record exact counts and any pre-existing warnings in the tracker. Do not implement over a failing baseline without first diagnosing it.

### Task 2: Add typed transport failures and endpoint-specific timeouts

**Files:**
- Create: `PersonalHealthEngine-L7/app/test/api_client_test.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/api_client.dart`

**Step 1:** Write failing tests using a local fake `http.Client` for 401, 5xx, invalid JSON, connection/client failure, normal read timeout, and long inference timeout selection.

**Step 2:** Run `flutter test test/api_client_test.dart` and confirm failures are caused by the missing error model/timeout behavior.

**Step 3:** Add `ApiErrorKind`, sanitized `ApiException.userMessage`, injectable `http.Client`, a normal read timeout, and a long inference timeout. Never expose response bodies for 401/5xx.

**Step 4:** Re-run the focused test and all Flutter tests; commit the task.

### Task 3: Secure production URL/token loading and migration

**Files:**
- Create: `PersonalHealthEngine-L7/app/lib/connection_store.dart`
- Create: `PersonalHealthEngine-L7/app/test/connection_store_test.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/main.dart`
- Modify: `PersonalHealthEngine-L7/app/pubspec.yaml`
- Modify: `PersonalHealthEngine-L7/app/pubspec.lock`

**Step 1:** Write failing tests for the HTTPS production default, build-defined initial token, secure-store preference, legacy SharedPreferences token migration/removal, URL persistence, and token rotation.

**Step 2:** Run the focused test and confirm the new store/API is absent.

**Step 3:** Implement a small storage interface backed by `flutter_secure_storage`; keep only `server.baseUrl` in SharedPreferences. Initialize the compile-time release token once and never log it.

**Step 4:** Re-run focused/all Flutter tests and commit.

### Task 4: Make every primary network screen terminate with retryable UX

**Files:**
- Create: `PersonalHealthEngine-L7/app/lib/widgets/api_error_view.dart`
- Create: `PersonalHealthEngine-L7/app/test/network_error_ui_test.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/today_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/history_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/patterns_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/qa_screen.dart`
- Modify: `PersonalHealthEngine-L7/app/lib/screens/me_screen.dart`

**Step 1:** Write failing widget tests for explicit Chinese error categories and visible retry actions on Today, History, Patterns, and Settings/status; add a Q&A failure assertion that contains no raw backend body.

**Step 2:** Run focused widget tests and verify expected failures.

**Step 3:** Add one shared compact error view and minimal per-screen loading/error state resets. Preserve cached Today while showing refresh failure non-destructively.

**Step 4:** Run focused/all Flutter tests, self-review against the six requested categories, and commit.

### Task 5: Configure Android production networking and release signing

**Files:**
- Modify: `PersonalHealthEngine-L7/app/android/app/src/main/AndroidManifest.xml`
- Modify: `PersonalHealthEngine-L7/app/android/app/build.gradle.kts`
- Create: `PersonalHealthEngine-L7/app/android/key.properties.example`
- Create: `deployment/scripts/build_android_release.ps1`
- Create: `deployment/tests/test_android_release_config.py`

**Step 1:** Write failing static tests asserting main-manifest INTERNET permission, no cleartext/TLS bypass, no debug signing fallback, ignored key material, and build script secret sourcing.

**Step 2:** Run the focused pytest and confirm the current debug-signing/permission failures.

**Step 3:** Implement key.properties-driven signing with a hard failure when release keys are missing; add release manifest hardening and a build script that reads secrets from environment/keyring and writes only ignored temporary files.

**Step 4:** Generate a local keystore under ignored `D:/PersonalHealthEngine/secrets/android`, store passwords in Windows keyring, run Flutter analyze/tests, build release, and verify the APK certificate with `apksigner`/`keytool`.

**Step 5:** Commit only source/config templates, never the keystore, passwords, token, generated defines, or APK.

### Task 6: Add reproducible Nginx and short-lived IP certificate deployment

**Files:**
- Create: `deployment/nginx/phe-mobile-http.conf`
- Create: `deployment/nginx/phe-mobile-https.conf`
- Create: `deployment/systemd/phe-certbot-renew.service`
- Create: `deployment/systemd/phe-certbot-renew.timer`
- Create: `deployment/scripts/install_mobile_gateway.sh`
- Create: `deployment/scripts/verify_mobile_gateway.py`
- Create: `deployment/tests/test_mobile_gateway_config.py`
- Modify: `deployment/README.md`

**Step 1:** Write failing static tests for listen 80/443 only, ACME webroot, redirect, public-IP certificate paths, localhost upstream, forwarded headers, 10-second connect and long inference read timeout, automatic renewal, and no 8707/11434 public bind.

**Step 2:** Run the focused pytest and confirm configs are absent.

**Step 3:** Implement idempotent installation: Nginx HTTP bootstrap, Certbot 5.4+ short-lived IP issuance, HTTPS activation, renewal timer, config validation, and reload. Do not modify L7 code or secrets.

**Step 4:** Run shell syntax/static tests and commit.

### Task 7: Deploy and accept the real public chain

**Files:**
- Deploy from the committed files to `/opt/phe`
- Runtime-only: `/etc/nginx`, `/etc/letsencrypt`, `/etc/systemd/system`

**Step 1:** Obtain authenticated SSH/cloud-console access. Back up only the exact Nginx/firewall/systemd targets before mutation.

**Step 2:** Audit OS, listeners, firewall, security group, Nginx/Certbot versions, and service state. Install the gateway and open only TCP 80/443 in host firewall and Alibaba security group.

**Step 3:** Verify internally: L7 health/auth/Today; proxy HTTPS health/auth/Today; Nginx SAN and chain.

**Step 4:** Verify externally from this Windows network: TLS trust, authenticated Today, History, Patterns, Settings, a bounded Q&A request, and closed 8707/11434.

**Step 5:** Verify Certbot dry-run/renewal service, Nginx reboot enablement, `phe-daily.service`, `phe-daily.timer`, Asia/Shanghai trigger, and SEALED L2-L6 integrity/checkpoints.

### Task 8: Build final APK and run Android-equivalent acceptance

**Files:**
- Runtime artifact: `D:/PersonalHealthEngine/artifacts/PHE-Android-production.apk`

**Step 1:** Build with the production HTTPS URL and keyring-backed API token; copy the signed result to the artifact path.

**Step 2:** Inspect manifest permissions, application ID/version, signature certificate, and absence of debug certificate.

**Step 3:** Install/run with adb if a device/emulator is available; otherwise run the release Dart e2e client against production and Android TLS-equivalent Java trust validation. Confirm Today parses and exits loading.

### Task 9: Final regression, secret audit, Git, and acceptance report

**Files:**
- Modify: `docs/plans/task.md`

**Step 1:** Run the full repository test baseline, Flutter analyze/tests, deployment tests, production smoke, port probes, APK signature checks, and service/timer/SEALED gates fresh.

**Step 2:** Review `git diff`, run indexed-file and staged-file secret audits, and verify no keystore/APK/private key/token is tracked.

**Step 3:** Commit with `feat: complete mainland China mobile production access for PHE`, push the feature branch, then integrate to `main` only after all tests pass and push `main` as requested.

**Step 4:** Report only evidence actually observed. Use `PHE MOBILE PRODUCTION = SEALED` only if public HTTPS, Android auth/Today, all requested APIs, tests, Git push, and protected ports all pass.

