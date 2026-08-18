"""Layer 6 daily reasoning materializer (deterministic replayable pipeline).

Assembles the Evidence Bundle, derives overall state, generates hypothesis candidates,
computes base confidence, invokes the reasoning adapter (mock by default), applies the
medical-review policy, and materializes everything with full provenance. Both full and
incremental modes share the same deterministic core.
"""

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from l6_core_v0_1 import (
    CONFIDENCE_DEFINITION_ID,
    CONTEXT_DEFINITION_ID,
    DAILY_DEFINITION_ID,
    EVIDENCE_DEFINITION_ID,
    HYPOTHESIS_DEFINITION_ID,
    MEDICAL_DEFINITION_ID,
    PATTERN_DEFINITION_ID,
    base_confidence,
    canonical_json,
    generate_candidates,
    load_definition,
    medical_trigger,
    overall_state,
    sha256_text,
    utc_now,
    validate_daily_output,
)
from l6_adapters_v0_1 import (
    ModelError,
    MockMedicalModelAdapter,
    MockReasoningModelAdapter,
    DeepSeekReasoningModelAdapter,
    MedGemmaMedicalModelAdapter,
)
from l6_evidence_v0_1 import assemble_evidence, bundle_sha256

PIPELINE = "l6.daily_reasoning"


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def register_definition(db, payload, checksum, definition_type, notes):
    row = db.execute(
        "SELECT definition_sha256 FROM definition_registry WHERE definition_id=? AND definition_version=?",
        (payload["definition_id"], payload["definition_version"]),
    ).fetchone()
    if row is None:
        db.execute(
            "INSERT INTO definition_registry (definition_id,definition_version,definition_type,status,definition_sha256,registered_at_utc,notes) VALUES (?,?,?,?,?,?,?)",
            (payload["definition_id"], payload["definition_version"], definition_type, "ACTIVE", checksum, utc_now(), notes),
        )
    elif row["definition_sha256"] != checksum:
        raise ValueError(f"definition checksum mismatch for {payload['definition_id']}")


def build_adapter(kind):
    if kind == "mock":
        return MockReasoningModelAdapter()
    if kind == "deepseek":
        return DeepSeekReasoningModelAdapter()
    raise ValueError(f"unknown reasoning adapter {kind}")


def build_medical_adapter(kind):
    if kind == "mock":
        return MockMedicalModelAdapter()
    if kind == "medgemma":
        return MedGemmaMedicalModelAdapter()
    raise ValueError(f"unknown medical adapter {kind}")


def read_recent_context(l6, analysis_date):
    rows = l6.execute(
        "SELECT context_type, context_date, body_part, severity FROM personal_context WHERE status='CURRENT' AND context_date <= ? ORDER BY context_date DESC LIMIT 20",
        (analysis_date,),
    ).fetchall()
    return [dict(row) for row in rows]


def read_recent_feedback(l6, analysis_date):
    rows = l6.execute(
        "SELECT subject_type, subject_id, feedback_status, correction_text, created_at_utc FROM user_feedback ORDER BY id DESC LIMIT 20"
    ).fetchall()
    return [dict(row) for row in rows]


def similar_cases(l6, recent_context, analysis_date):
    types = [c["context_type"] for c in recent_context]
    if not types:
        return []
    placeholders = ",".join("?" for _ in types)
    rows = l6.execute(
        f"SELECT context_type, context_date FROM personal_context WHERE status='CURRENT' AND context_type IN ({placeholders}) AND context_date < ? ORDER BY context_date DESC LIMIT 10",
        (*types, analysis_date),
    ).fetchall()
    return [dict(row) for row in rows]


def reconcile_daily(l6, analysis_date, bundle, bundle_hash, hypotheses, daily, provenance, model_invocations, now):
    # idempotency: reuse if identical
    existing = l6.execute(
        "SELECT id, bundle_sha256 FROM evidence_bundles WHERE analysis_date=? AND evidence_definition_id=? AND evidence_definition_version=? AND status='CURRENT'",
        (analysis_date, EVIDENCE_DEFINITION_ID, "0.1"),
    ).fetchone()
    if existing and existing["bundle_sha256"] == bundle_hash:
        return {"inserted": 0, "stale": 0, "bundle_id": existing["id"]}

    if existing:
        l6.execute("UPDATE evidence_bundles SET status='STALE', updated_at_utc=? WHERE id=?", (now, existing["id"]))
        l6.execute("UPDATE hypotheses SET status='STALE', updated_at_utc=? WHERE analysis_date=? AND status='CURRENT'", (now, analysis_date))
        l6.execute("UPDATE daily_reasoning SET status='STALE', updated_at_utc=? WHERE analysis_date=? AND status='CURRENT'", (now, analysis_date))

    cursor = l6.execute(
        "INSERT INTO evidence_bundles (analysis_date,bundle_json,bundle_sha256,evidence_definition_id,evidence_definition_version,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,'CURRENT',?,?)",
        (analysis_date, canonical_json(bundle), bundle_hash, EVIDENCE_DEFINITION_ID, "0.1", now, now),
    )
    bundle_id = cursor.lastrowid

    for rank, hyp in enumerate(hypotheses, start=1):
        l6.execute(
            "INSERT INTO hypotheses (analysis_date,evidence_bundle_id,hypothesis_type,rank,supporting_json,counter_json,missing_json,confidence,reasoning_summary,origin,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?)",
            (
                analysis_date, bundle_id, hyp["hypothesis_type"], rank,
                canonical_json(hyp.get("supporting", [])), canonical_json(hyp.get("counter", [])),
                canonical_json(hyp.get("missing", [])), hyp["confidence"],
                hyp.get("reasoning_summary"), hyp.get("origin", "MODEL"), now, now,
            ),
        )

    l6.execute(
        "INSERT INTO daily_reasoning (analysis_date,evidence_bundle_id,overall_state,primary_hypothesis_type,secondary_hypothesis_type,confidence,recommended_actions_json,medical_review_state,reasoning_model,medical_model,reasoning_summary,definition_id,definition_version,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT',?,?)",
        (
            analysis_date, bundle_id, daily["overall_state"], daily["primary_hypothesis_type"],
            daily["secondary_hypothesis_type"], daily["confidence"],
            canonical_json(daily["recommended_actions"]), daily["medical_review_state"],
            daily["reasoning_model"], daily["medical_model"], daily["reasoning_summary"],
            DAILY_DEFINITION_ID, "0.1", now, now,
        ),
    )

    for p in provenance:
        l6.execute(
            "INSERT OR IGNORE INTO reasoning_provenance (subject_type,subject_id,upstream_layer,upstream_type,upstream_id,created_at_utc) VALUES ('EVIDENCE_BUNDLE',?,?,?,?,?)",
            (bundle_id, p["layer"], p["type"], p["id"], now),
        )

    for inv in model_invocations:
        l6.execute(
            "INSERT INTO model_invocations (adapter_kind,model_id,request_sha256,response_sha256,status,token_input,token_output,duration_ms,error_code,created_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                inv["adapter_kind"], inv["model_id"], inv["request_sha256"], inv["response_sha256"],
                inv["status"], inv.get("token_input"), inv.get("token_output"),
                inv.get("duration_ms"), inv.get("error_code"), now,
            ),
        )
    return {"inserted": 1, "stale": 1 if existing else 0, "bundle_id": bundle_id}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("full", "incremental", "replay"), required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--l6", required=True)
    parser.add_argument("--analysis-date", default=None)
    parser.add_argument("--reasoning-adapter", choices=("mock", "deepseek"), default="mock")
    parser.add_argument("--medical-adapter", choices=("mock", "medgemma"), default="mock")
    parser.add_argument("--context", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--confidence", required=True)
    parser.add_argument("--daily", required=True)
    parser.add_argument("--medical", required=True)
    parser.add_argument("--pattern", required=True)
    args = parser.parse_args()

    defs = {}
    defs["context"], sha = load_definition(Path(args.context), CONTEXT_DEFINITION_ID)
    defs["evidence"], esha = load_definition(Path(args.evidence), EVIDENCE_DEFINITION_ID)
    defs["hypothesis"], hsha = load_definition(Path(args.hypothesis), HYPOTHESIS_DEFINITION_ID)
    defs["confidence"], csha = load_definition(Path(args.confidence), CONFIDENCE_DEFINITION_ID)
    defs["daily"], dsha = load_definition(Path(args.daily), DAILY_DEFINITION_ID)
    defs["medical"], msha = load_definition(Path(args.medical), MEDICAL_DEFINITION_ID)
    defs["pattern"], psha = load_definition(Path(args.pattern), PATTERN_DEFINITION_ID)

    l3 = sqlite3.connect(readonly_uri(args.l3), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(args.l4), uri=True)
    l4.row_factory = sqlite3.Row
    l5 = sqlite3.connect(readonly_uri(args.l5), uri=True)
    l5.row_factory = sqlite3.Row
    l6 = sqlite3.connect(args.l6)
    l6.row_factory = sqlite3.Row
    l6.execute("PRAGMA foreign_keys = ON")

    run_id = f"l6-{args.mode}-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    try:
        if l6.execute("PRAGMA user_version").fetchone()[0] < 2:
            raise RuntimeError("L6 materializer requires schema >= 2")

        analysis_date = args.analysis_date
        if analysis_date is None:
            analysis_date = l5.execute("SELECT MAX(feature_date) FROM deviation_analytics WHERE status='CURRENT'").fetchone()[0]

        l6.execute("BEGIN IMMEDIATE")
        now = utc_now()
        register_definition(l6, defs["context"], sha, "CONTEXT_EXTRACTION", "L6 context extraction")
        register_definition(l6, defs["evidence"], esha, "EVIDENCE_ASSEMBLY", "L6 evidence assembly")
        register_definition(l6, defs["hypothesis"], hsha, "HYPOTHESIS", "L6 hypothesis framework")
        register_definition(l6, defs["confidence"], csha, "CONFIDENCE", "L6 confidence policy")
        register_definition(l6, defs["daily"], dsha, "DAILY_REASONING", "L6 daily reasoning")
        register_definition(l6, defs["medical"], msha, "MEDICAL_REVIEW", "L6 medical review policy")
        register_definition(l6, defs["pattern"], psha, "PERSONAL_PATTERN", "L6 personal pattern policy")

        recent_context = read_recent_context(l6, analysis_date)
        cases = similar_cases(l6, recent_context, analysis_date)

        # Feedback informs Personal Pattern learning (a separate step), not the same-day
        # deterministic bundle, so the daily reasoning remains a pure replay of data/context.
        bundle, provenance = assemble_evidence(l3, l4, l5, analysis_date, recent_context, [], cases)
        bundle["overall_state"] = overall_state(bundle)
        candidates = generate_candidates(bundle)

        reasoning_adapter = build_adapter(args.reasoning_adapter)
        reasoning_model_id = reasoning_adapter.model_id

        # Deterministic confidence per candidate.
        for cand in candidates:
            support = len(cand.get("supporting", []))
            cand["confidence"] = base_confidence(support, 0, bundle["overall_state"] == "INSUFFICIENT_EVIDENCE", bool(recent_context))
            cand["origin"] = "CANDIDATE"

        # Rank candidates deterministically (mock picks primary=first, secondary=second).
        ranked = candidates[:]
        primary = ranked[0] if ranked else {"hypothesis_type": "UNKNOWN", "confidence": "VERY_LOW"}
        secondary = ranked[1] if len(ranked) > 1 else None

        request_payload = {"bundle": bundle, "candidates": [c["hypothesis_type"] for c in ranked]}
        request_hash = sha256_text(canonical_json(request_payload))
        invocations = []

        model_output = None
        reasoning_error = None
        try:
            model_output = reasoning_adapter.reason_daily(bundle, ranked)
        except ModelError as exc:
            reasoning_error = str(exc)
            model_output = None

        if model_output is not None:
            ok, errors = validate_daily_output(model_output, [c["hypothesis_type"] for c in ranked] + ["UNKNOWN"], primary["confidence"])
            if not ok:
                model_output = None
                reasoning_error = "invalid model output: " + "; ".join(errors)
                invocations.append({"adapter_kind": "REASONING", "model_id": reasoning_model_id, "request_sha256": request_hash, "response_sha256": None, "status": "INVALID", "error_code": "INVALID_OUTPUT"})
            else:
                invocations.append({"adapter_kind": "REASONING", "model_id": reasoning_model_id, "request_sha256": request_hash, "response_sha256": sha256_text(canonical_json(model_output)), "status": "PASS"})
        else:
            invocations.append({"adapter_kind": "REASONING", "model_id": reasoning_model_id, "request_sha256": request_hash, "response_sha256": None, "status": "UNAVAILABLE", "error_code": "REASONING_UNAVAILABLE"})

        if model_output is None:
            # Safe fallback: keep deterministic evidence; no fabricated answer.
            primary_type = primary["hypothesis_type"]
            secondary_type = secondary["hypothesis_type"] if secondary else None
            confidence = primary["confidence"]
            summary = "（推理模型暂不可用，仅保留结构化证据。）" if reasoning_error else None
            actions = []
        else:
            primary_type = model_output["primary_hypothesis_type"]
            secondary_type = model_output["secondary_hypothesis_type"]
            # Model may only downgrade, never upgrade, the deterministic confidence.
            model_conf = model_output.get("confidence")
            order = ["VERY_LOW", "LOW", "MODERATE", "HIGH"]
            if order.index(model_conf) <= order.index(primary["confidence"]):
                confidence = model_conf
            else:
                confidence = primary["confidence"]
            summary = model_output["reasoning_summary"]
            actions = model_output["recommended_actions"]

        # Medical review policy.
        review_state, reasons = medical_trigger(None, bundle, [primary_type, secondary_type] if secondary_type else [primary_type])
        medical_model_id = None
        if review_state == "REQUIRED":
            medical_adapter = build_medical_adapter(args.medical_adapter)
            medical_model_id = medical_adapter.model_id
            try:
                findings = medical_adapter.review(bundle, [primary_type, secondary_type] if secondary_type else [primary_type])
                review_state = "PERFORMED"
                l6.execute(
                    "INSERT INTO medical_reviews (subject_type,subject_id,review_state,trigger_reason,findings_json,reviewer_model,created_at_utc) VALUES ('DAILY_REASONING',?,?,?,?,?,?)",
                    (-1, review_state, canonical_json(reasons), canonical_json(findings), medical_model_id, now),
                )
            except ModelError as exc:
                review_state = "UNAVAILABLE"
                invocations.append({"adapter_kind": "MEDICAL", "model_id": medical_model_id, "request_sha256": request_hash, "response_sha256": None, "status": "UNAVAILABLE", "error_code": "MEDICAL_REVIEW_UNAVAILABLE"})
        else:
            medical_model_id = None

        daily = {
            "overall_state": bundle["overall_state"],
            "primary_hypothesis_type": primary_type,
            "secondary_hypothesis_type": secondary_type,
            "confidence": confidence,
            "recommended_actions": actions,
            "medical_review_state": review_state,
            "reasoning_model": reasoning_model_id,
            "medical_model": medical_model_id,
            "reasoning_summary": summary,
        }

        # Attach counter/missing evidence deterministically.
        for cand in ranked:
            cand["counter"] = []
            cand["missing"] = list(bundle["missing_evidence"])
            cand["reasoning_summary"] = summary if cand is primary else None

        result = reconcile_daily(l6, analysis_date, bundle, bundle_sha256(bundle), ranked, daily, provenance, invocations, now)

        l6.execute(
            "INSERT INTO pipeline_runs (run_id,mode,status,source_l3_path,source_l4_path,source_l5_path,started_at_utc,finished_at_utc,details_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, {"full": "FULL_REBUILD", "incremental": "INCREMENTAL", "replay": "REPLAY"}[args.mode], "PASS", str(Path(args.l3).resolve()), str(Path(args.l4).resolve()), str(Path(args.l5).resolve()), now, now, canonical_json(result)),
        )
        l6.execute(
            "INSERT INTO processing_checkpoints (pipeline_name,last_l5_analytic_id,last_l3_feature_id,last_l4_baseline_id,last_successful_run_id,updated_at_utc) VALUES (?,?,?,?,?,?) ON CONFLICT(pipeline_name) DO UPDATE SET last_l5_analytic_id=excluded.last_l5_analytic_id,last_l3_feature_id=excluded.last_l3_feature_id,last_l4_baseline_id=excluded.last_l4_baseline_id,last_successful_run_id=excluded.last_successful_run_id,updated_at_utc=excluded.updated_at_utc",
            (PIPELINE, l5.execute("SELECT COALESCE(MAX(id),0) FROM deviation_analytics").fetchone()[0], l3.execute("SELECT COALESCE(MAX(id),0) FROM derived_features").fetchone()[0], l4.execute("SELECT COALESCE(MAX(id),0) FROM rolling_baselines").fetchone()[0], run_id, now),
        )
        if l6.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("L6 foreign key check failed")
        l6.commit()
        print(json.dumps({"status": "PASS", "mode": args.mode.upper(), "analysis_date": analysis_date, "overall_state": bundle["overall_state"], "primary_hypothesis": primary_type, "medical_review_state": review_state, "bundle_sha256": bundle_sha256(bundle), "result": result}, indent=2))
    except Exception:
        l6.rollback()
        raise
    finally:
        l3.close()
        l4.close()
        l5.close()
        l6.close()


if __name__ == "__main__":
    main()
