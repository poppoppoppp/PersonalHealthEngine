# PHE Original Product Contract Conformance Audit

## Verdict

Implementation and production conformance are PASS. The current health judgment remains E
because the SEALED L6 policy required medical review for persistent notable
anomalies and the SEALED L7 mapping gives performed medical review E precedence.
No test or code path in this repair reclassified that judgment.

The repaired backend is deployed at `/opt/phe`, authenticated Today and exact-evidence
readbacks pass, and the public HTTPS gateway remains reachable without exposing ports
8707 or 11434. The final signed APK passes artifact acceptance.

## Authority reviewed

- `PersonalHealthEngine-L6/L6_CONTRACT.md`, seal, handoff, and final audit.
- `PersonalHealthEngine-L7/L7_TECHNICAL_ARCHITECTURE.md`, seal, handoff, final
  audit, and implementation phases.
- User-supplied conformance and final-presentation acceptance requirements.

The original Layer 7 Product Contract body is absent from Git history and the
provided attachments. This is a source-document gap, not an inferred product
redesign: the formally SEALED L7 derivative documents contain the five-state,
information-order, threshold, model-ownership, semantic-stability, evidence, and
notification requirements used here.

## Contract matches preserved

| Area | Evidence | Result |
|---|---|---|
| Five fixed states | Renderer tests cover A-E labels | PASS |
| E precedence | Medical-review and symptom paths remain E | PASS |
| Information order | E conclusion-action-cause; non-E conclusion-cause-action | PASS |
| Three thresholds | Recompute, presentation, and notification remain separate | PASS |
| No scores/diagnosis | Renderer/API tests and UI scan | PASS |
| Max three actions; stable zero | Backend and Flutter tests | PASS |
| Append-only history | Presentation repair inserts a new row and retains the prior row | PASS |
| Model-owned reasoning | L7 constrains language but does not re-derive health judgment | PASS |

## Defects repaired

| Defect | Repair | Verification |
|---|---|---|
| Real daily output could be English | Versioned L7 DeepSeek product adapter requires validated Simplified Chinese | Mixed-English/raw-enum rejection test PASS |
| Q&A reused daily shape and dropped reason | Dedicated answer/reason/actions schema and response mapping | Focused Q&A tests PASS |
| MedGemma Q&A call violated adapter protocol | Optional hypothesis-types and question parameters restored | Real-adapter runtime test PASS |
| Secondary cause and other surfaces leaked enums | Canonical Chinese label module plus explicit API label fields | Backend and Flutter conformance tests PASS |
| Sleep evidence collapsed to `睡眠` | Feature-specific labels for duration, stage duration, and proportions | Distinct-sleep regression PASS |
| Evidence drilldown substituted rows by feature name | Exact L6 provenance L5 ID, then exact L3/L4 foreign keys | Exact-ID regression PASS |
| Evidence lacked magnitude and age | Display values, baseline values, feature date, and freshness added | API/widget tests PASS |
| Same judgment retained stale update time | Response-only time fields refresh; stored semantic copy remains stable | Timestamp regression PASS |
| Legacy English Today would persist forever | Append-only presentation-contract migration, same signature, no notification | Migration regression PASS |
| Version/history/context/pattern displays exposed machine values | Backend labels and label-only Flutter rendering | Widget tests and source scan PASS |

## Contract gaps filled minimally

- User-facing labels for the sealed L6 hypothesis and context vocabularies were not
  enumerated in the available product-contract source. The repair adds deterministic
  labels without changing machine values.
- User-facing labels for concrete L3 feature names were not formally specified. The
  repair uses the smallest precise descriptions, such as `平均血氧`, `静息心率`,
  `本次睡眠时长`, `REM 睡眠占比`, and `记录到的累计步数`.
- A presentation contract version was not stored in the SQL schema. It is embedded in
  `rendered_json`, avoiding a schema migration and preserving append-only history.

## Upstream limitations disclosed, not redesigned

- SEALED L6 selects the latest deviation per feature/source within its permitted
  lookback. A single Today bundle can therefore contain evidence dates older than the
  analysis date. Every displayed fact now states its own date and freshness.
- `steps.daily.sum` source semantics do not prove that a same-day low total represents
  a completed day. The display says `记录到的累计步数` and exposes its date; L4/L5
  analytics were not changed.
- Baseline maturity and evidence-status machine values remain available to API clients,
  while the owner sees plain Chinese descriptions.

## Local acceptance evidence

- Final formal Python baseline after the DeepSeek cutover:
  `197 passed, 2 skipped, 982 warnings, 3 subtests passed` in 100.61 seconds.
- L7 backend: `103 passed`; the full baseline includes the subsequent mixed-language
  gate, bringing the new focused conformance group to 13 passing tests.
- Flutter: `31 passed`.
- Flutter analyzer: 0 errors, 0 warnings, 17 pre-existing info-level notices with
  `--no-fatal-infos`.
- Deterministic visible-copy scan: `A-E_VISIBLE_COPY_SCAN_PASS`.
- Flutter surface scan: no direct display access for hypothesis, context, body-part,
  baseline-maturity, evidence-status, overall-state, primary-cause, feedback-status,
  product-state, or trigger enums. The only remaining deviation enum access selects a
  color/direction branch and is not rendered.

## Production and artifact gates

| Gate | Status | Evidence |
|---|---|---|
| VPS backend deployment | PASS | `/opt/phe/.deployed-commit` is `8d5efaa94aec788e6a00c3b2bcb18c1364e96456`; local container health is OK |
| Authenticated production APIs | PASS | Today and exact evidence drill-down smoke tests pass against the running container |
| Signed Android APK | PASS | `D:\PersonalHealthEngine\artifacts\PHE-Android-production.apk`, 50,337,217 bytes, V2 signature valid, one signer `CN=Personal Health Engine, O=Private, C=CN`, package `com.personalhealthengine.phe_app`, INTERNET present, backup disabled, production HTTPS endpoint embedded |
| APK SHA-256 | PASS | `3BA779CAEC2B454364F6099545247D2F5AB483EECC4A39CB7DD4CBA61D3D1AC9` |
| Secret scan | PASS | No tracked key/token candidates; two matches are Gradle property lookups and `__LOCAL_KEYRING_VALUE__` placeholders; temporary signing/define files absent |
| Public gateway | PASS | Trusted HTTPS returns 200; 8707 and 11434 remain unreachable publicly; certificate renewal timer is active and enabled |
| Daily automation | PASS | `phe-daily.service` last result is success; timer is active and enabled |
| Git commit and push | PASS | Production commit is present on `origin/main` |

## DeepSeek production cutover

- Repository default, `/etc/phe/runtime.env`, systemd environment source, and the
  running container all select `deepseek-v4-flash`; the legacy effort variable is absent.
- The adapter sends explicit `thinking={"type":"disabled"}` and fails closed if the
  requested or returned model identifier differs from Flash.
- Isolated production-code acceptance made real Today, Q&A, and Context calls. All
  three invocation records returned `deepseek-v4-flash`, all persisted model
  identifiers were Flash, and post-cutover Pro-call count was zero.
- Feedback has no independent model path; correction reuses Context extraction.
- MedGemma configuration remained byte-for-byte equivalent across the deployment.
