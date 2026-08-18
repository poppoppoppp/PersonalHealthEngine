"""Layer 6 interactive health question answering.

Builds a deterministic Question Evidence Bundle from the current state, then answers via the
reasoning adapter (mock by default). Medical-review policy applies per the question content.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from l6_core_v0_1 import (
    EVIDENCE_DEFINITION_ID,
    canonical_json,
    generate_candidates,
    load_definition,
    medical_trigger,
    overall_state,
    sha256_text,
    utc_now,
)
from l6_adapters_v0_1 import ModelError, MockMedicalModelAdapter, MockReasoningModelAdapter
from l6_evidence_v0_1 import assemble_evidence


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--l6", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--hypothesis", required=True)
    args = parser.parse_args()

    evidence_def, _ = load_definition(Path(args.evidence), EVIDENCE_DEFINITION_ID)
    load_definition(Path(args.hypothesis), "l6.hypothesis")

    l3 = sqlite3.connect(readonly_uri(args.l3), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(args.l4), uri=True)
    l4.row_factory = sqlite3.Row
    l5 = sqlite3.connect(readonly_uri(args.l5), uri=True)
    l5.row_factory = sqlite3.Row
    l6 = sqlite3.connect(args.l6)
    l6.row_factory = sqlite3.Row
    l6.execute("PRAGMA foreign_keys = ON")
    try:
        analysis_date = l5.execute("SELECT MAX(feature_date) FROM deviation_analytics WHERE status='CURRENT'").fetchone()[0]
        recent_context = [dict(row) for row in l6.execute("SELECT context_type,context_date,body_part,severity FROM personal_context WHERE status='CURRENT' AND context_date <= ? ORDER BY context_date DESC LIMIT 20", (analysis_date,))]
        recent_feedback = [dict(row) for row in l6.execute("SELECT subject_type,subject_id,feedback_status FROM user_feedback ORDER BY id DESC LIMIT 20")]
        bundle, provenance = assemble_evidence(l3, l4, l5, analysis_date, recent_context, recent_feedback, [])
        bundle["overall_state"] = overall_state(bundle)
        candidates = generate_candidates(bundle)

        adapter = MockReasoningModelAdapter()
        answer = adapter.answer_question(args.question, bundle, candidates)

        hypothesis_types = [c["hypothesis_type"] for c in candidates]
        review_state, reasons = medical_trigger(args.question, bundle, hypothesis_types)
        reviewer_model = None
        if review_state == "REQUIRED":
            reviewer = MockMedicalModelAdapter()
            reviewer_model = reviewer.model_id
            try:
                reviewer.review(bundle, hypothesis_types, args.question)
                review_state = "PERFORMED"
            except ModelError:
                review_state = "UNAVAILABLE"
        else:
            review_state = "BYPASSED"

        now = utc_now()
        question_bundle = {"analysis_date": analysis_date, "overall_state": bundle["overall_state"], "candidates": hypothesis_types}
        qhash = sha256_text(canonical_json(question_bundle))
        db2 = l6
        db2.execute("BEGIN IMMEDIATE")
        cursor = db2.execute(
            "INSERT INTO qa_sessions (question_text,asked_at_utc,evidence_bundle_id,question_bundle_sha256,answer_json,answer_text,medical_review_state,reasoning_model,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (args.question, now, None, qhash, canonical_json(answer), answer["answer_text"], review_state, adapter.model_id, "CURRENT", now, now),
        )
        qa_id = cursor.lastrowid
        db2.execute(
            "INSERT INTO medical_reviews (subject_type,subject_id,review_state,trigger_reason,findings_json,reviewer_model,created_at_utc) VALUES ('QA',?,?,?,?,?,?)",
            (qa_id, review_state, canonical_json(reasons), canonical_json({"no_diagnosis": True}), reviewer_model, now),
        )
        db2.commit()
        print(json.dumps({"status": "PASS", "qa_id": qa_id, "overall_state": bundle["overall_state"], "answer": answer["answer_text"], "medical_review_state": review_state}, ensure_ascii=False, indent=2))
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
