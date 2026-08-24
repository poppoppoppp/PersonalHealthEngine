# PHE Original Product Contract Conformance Design

## Authority and scope

This repair preserves the SEALED L1-L6 algorithms, L6 reasoning ownership, L7
state machine, append-only history, and product-state precedence. The original
Layer 7 Product Contract body is not present in the repository or supplied
attachments; its formally sealed derivatives are present in
`L7_TECHNICAL_ARCHITECTURE.md`, `L7_SEAL.md`, `L7_HANDOFF.md`, and
`L7_FINAL_AUDIT.json`. Those documents, the L6 contract, and the user's explicit
acceptance requirements are the authority for this defect repair.

The current production E state is not reclassified. Its persistent anomaly
trigger required medical review, and the sealed L7 mapping assigns a performed
medical review to E. The repair changes presentation fidelity and provenance,
not the health judgment.

## Audited conformance matrix

| Contract area | Current evidence | Conformance before repair | Required repair |
|---|---|---:|---|
| Today five states and E precedence | Backend state machine renders A-E and prioritizes E; production is E after performed medical review | Yes | Preserve and regression-test |
| Information order | E is conclusion-action-cause; non-E is conclusion-cause-action | Yes | Preserve and regression-test |
| Three-threshold wording | Renderer has state-specific threshold copy | Yes | Preserve, ensure Chinese only |
| Simplified-Chinese product copy | Real DeepSeek prompt does not require Chinese; production reasoning/actions are English | No | L7 product adapter requires zh-CN; migrate current presentation append-only |
| Raw-enum isolation | Secondary cause, context, history, evidence status, version sheet, and patterns leak machine enums | No | Shared deterministic Chinese label layer; raw fields remain machine-only |
| Stable semantic copy | Today versions persist rendered payload for the same judgment | Partial | Keep copy stable while refreshing only time fields |
| Update timestamp semantics | Same-signature read returns the persisted old timestamp | No | Refresh `updated_at`/`data_as_of` without creating a semantic version |
| Judgment-update semantics | Same signature may retain a stale `judgment_updated` flag | No | Always return false unless semantic signature changes |
| Evidence L2 clarity | Multiple sleep features collapse to the single label `睡眠` | No | Feature-specific labels, direction, magnitude, date, and freshness |
| Evidence provenance | Drilldown re-queries by feature name and can show a different date/window | No | Resolve exact L5 deviation and exact L3/L4 IDs from L6 provenance |
| Evidence data freshness | L6 snapshot may contain latest-per-source evidence from different dates | Contract/upstream limitation | Expose per-item date/freshness; do not imply every item is today |
| Partial-day steps | Source semantics do not prove that a low same-day total is final-day activity | Contract/upstream limitation | Label as recorded cumulative steps and expose evidence date |
| Model-owned reasoning | Renderer passes L6 summary/actions verbatim | Yes, but language mismatch | Keep model ownership; constrain output language at L7 adapter boundary |
| Q&A structured output | Daily schema is reused and reason is dropped | No | Dedicated Chinese Q&A schema with answer, reason, and actions |
| MedGemma Q&A review | Real adapter signature is incompatible with the sealed protocol | No | Restore protocol-compatible optional hypothesis/question parameters |
| Patterns/history/context labels | Several sealed L6 values lack labels and can leak raw | No | Canonical labels in API payloads and Flutter rendering |
| Notification semantics | Notifications are tied to `judgment_updated` | Yes | Presentation-only migration must not notify |

## Considered approaches

1. Translate or map strings only in Flutter. This would leave API consumers,
   stored versions, notifications, Q&A, and evidence provenance non-conformant.
2. Edit the SEALED L6 reasoning contract directly. This would cross the layer
   boundary and turn a product-language defect into an upstream contract change.
3. Add an L7 product adapter and deterministic presentation/provenance layer.
   This keeps L6 ownership of reasoning, makes all product surfaces consistent,
   and permits an append-only repair of the already stored English Today.

Approach 3 is selected.

## Architecture

### Product-language boundary

L7 wraps the real DeepSeek adapter. Daily reasoning keeps the sealed L6 schema
but adds a strict Simplified-Chinese product-output instruction. Q&A uses a
dedicated structured schema: `answer_text`, `reasoning_summary`, and at most
three `recommended_actions`. The adapter contract version participates in the
model-cache request hash so an old English cache entry cannot silently reappear.

Existing stored English output is translated once by the same configured model.
The new rendered payload is appended as a presentation-repair Today version with
the same semantic signature and `judgment_updated=false`. Failure to obtain valid
Chinese never changes the judgment and never exposes an unverified client-side
translation.

### Canonical presentation labels

A single L7 labels module owns Chinese display labels for hypotheses, contexts,
body parts, confidence, state, feedback status, evidence status, baseline
maturity, triggers, outcome signals, and feature names. API payloads expose both
raw machine fields and explicit `*_label` fields. Flutter displays only the label
fields, with a neutral Chinese fallback rather than the raw enum.

Feature labels are the smallest deterministic contract-gap fill needed for the
current sealed vocabulary. Examples include `平均血氧`, `静息心率`, `本次睡眠时长`,
`REM 睡眠占比`, `睡眠中清醒时长`, and `记录到的累计步数`.

### Exact evidence chain

The current L6 bundle's `reasoning_provenance` is authoritative. For each evidence
item L7 resolves the exact L5 deviation ID, then its exact L3 feature ID and L4
baseline ID. Level 2 renders a concise fact containing the feature label,
direction, current value, personal baseline, feature date, and freshness. Level 3
uses the same IDs and may add the matching series history; it must not substitute
another deviation window merely because the feature name matches.

### Semantic stability and time

The semantic signature continues to control health-judgment versions. Same
signature means no new judgment version and no notification. The stored conclusion,
cause, actions, and evidence text remain byte-stable, while response-only time
fields are refreshed and `judgment_updated` is false. A presentation contract
version permits exactly one append-only repair of legacy stored payloads without
claiming that the health judgment changed.

## Acceptance

Automated tests must first demonstrate each defect, then cover both E and non-E
ordering, Chinese-only user copy, raw-enum isolation, exact evidence IDs, distinct
sleep facts, date/freshness disclosure, stable semantic copy with refreshed time,
presentation-only migration, structured Q&A, and MedGemma protocol compatibility.
Full Python and Flutter baselines, production API acceptance, signed APK inspection,
secret scanning, Git commit/push, and VPS deployment are the final gates. Missing
external SSH credentials are recorded as an external deployment blocker only after
all local and public-API alternatives are exhausted.
