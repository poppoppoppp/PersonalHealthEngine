# PHE Mobile Production Access Design

## Scope and invariants

Complete the Android-to-L7 production path without changing any SEALED L1-L7
algorithm, schema, reasoning contract, or acquisition behavior. L7 remains bound to
`127.0.0.1:8707`, Ollama remains non-public on `11434`, and only deployment,
networking, mobile transport, mobile credential handling, error UX, and release
packaging may change.

## Audited root cause

The Android client defaults to `http://10.0.2.2:8707` and the local development
token. That address exists only inside an Android emulator. The release manifest also
lacks the Android `INTERNET` permission, and the release build is signed with the
debug key. Network calls use one long overall timeout and most secondary screens show
raw exceptions without a retry state. The production VPS correctly rejects public
connections to 8707 and 11434, while public 80 and 443 currently time out.

## Considered production entry options

1. An existing filed domain with ordinary 90-day TLS would be the conventional
   choice, but no domain, DNS, certificate, or proxy exists in the repository or local
   machine, and the Alibaba Cloud console is not currently authenticated.
2. A publicly trusted IP-address certificate is now generally available from Let's
   Encrypt. Certbot 5.4+ supports IPv4 issuance with the `shortlived` profile and
   webroot validation. This requires no domain, DNS, custom CA, VPN, tunnel, or client
   TLS bypass.
3. Cloudflare Tunnel, a phone VPN, self-signed TLS, or direct public 8707 exposure
   violate the requested mainland-China reliability or security boundary.

Option 2 is selected. The endpoint is `https://47.111.229.39` on the existing Alibaba
Cloud mainland-China ECS address.

## Production gateway

Nginx listens on public 80/443. Port 80 serves only the ACME HTTP-01 webroot and an
HTTPS redirect. Port 443 terminates a Let's Encrypt IP certificate and proxies to
`http://127.0.0.1:8707` with `X-Forwarded-*` headers. Upstream connect timeout is
short, while response timeout permits MedGemma/DeepSeek work. Default Nginx logs do
not include Authorization headers or request bodies.

The IP certificate is valid for roughly six days, so a systemd timer runs Certbot
renewal twice daily and reloads Nginx only after successful renewal. Deployment
verification checks the timer, Nginx config, certificate SAN/trust, local proxy API,
and externally reachable ports. Alibaba security-group ingress is restricted to TCP
80/443 (and existing administrative SSH); 8707/11434 remain absent.

## Android configuration and authentication

The production URL is a non-secret compile-time default and may be overridden with
`PHE_API_BASE_URL`. The bearer token is supplied only through an untracked build
define sourced from Windows Credential Manager/keyring. On first launch it is copied
to Android Keystore-backed Flutter secure storage; connection edits and token
rotation update secure storage, while SharedPreferences holds only the non-secret
URL. The old SharedPreferences token is migrated once and removed.

This is a single-owner private APK. Any credential shipped to an unattended mobile
client is recoverable by a sufficiently privileged device owner, so obfuscation is
not treated as a security boundary. The practical boundary is no token in Git/logs,
OS-backed storage at rest, TLS in transit, server-side rotation, and a non-public L7
port. A multi-user enrollment service would be a materially larger authentication
product and is outside the SEALED transport change.

## Mobile transport and UX

The API client maps transport failures into stable user-facing categories:
no network, cannot connect, authentication failure, server failure, timeout, and
invalid response. Read-only endpoints use a bounded normal timeout; inference paths
use a separate long timeout. Backend bodies and tracebacks are never rendered.

Today, History, Personal Patterns, Settings/status, and Q&A each transition from
loading to either data or an explicit error state. Read screens expose a retry button.
Q&A preserves the user's question and renders a retryable failure message without a
Python traceback. Changing the connection notifies all root screens so they reload
against the new client.

## Release and acceptance

A dedicated upload/release keystore is generated under ignored local `secrets/`, with
passwords stored in the Windows credential backend. Gradle reads an ignored
`key.properties`; release builds never fall back to debug signing. The final artifact
is copied to `D:\PersonalHealthEngine\artifacts\PHE-Android-production.apk`.

Acceptance requires backend tests, Flutter analyze/tests, signed APK build and
signature verification, HTTPS trust from an external network, authenticated health
and real Today/History/Patterns/Settings/Q&A calls, Android-equivalent parsing, public
port probes, and a fresh check of the daily service/timer and SEALED database gates.

