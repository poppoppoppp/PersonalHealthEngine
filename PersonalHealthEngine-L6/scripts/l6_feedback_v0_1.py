"""Layer 6 User Feedback recording + Personal Pattern learning.

Feedback is stored as USER_FEEDBACK (never promoted to fact). CONFIRMED feedback advances the
corresponding trigger->outcome Personal Pattern support; REJECTED/CORRECTED only advance the
observation count. A CORRECTED feedback also ingests the correction text as new context.
"""

import argparse
import json
import sqlite3
from pathlib import Path

from l6_core_v0_1 import canonical_json, utc_now
from l6_adapters_v0_1 import MockReasoningModelAdapter

PATTERN_ESTABLISHED_MIN_SUPPORT = 3


def outcome_signals(bundle):
    signals = set()
    for d in bundle.get("deviations", []):
        if d.get("deviation_class") == "ABOVE_TYPICAL_RANGE":
            signals.add(f"{d.get('metric')}_UP")
        elif d.get("deviation_class") == "BELOW_TYPICAL_RANGE":
            signals.add(f"{d.get('metric')}_DOWN")
    return signals


def update_patterns(db, bundle, feedback_status, now):
    triggers = [c.get("context_type") for c in bundle.get("recent_context", [])]
    signals = sorted(outcome_signals(bundle))
    if not triggers or not signals:
        return
    for trigger in triggers:
        for signal in signals:
            key = f"{trigger}::{signal}"
            row = db.execute("SELECT id,support_count,total_count FROM personal_patterns WHERE pattern_key=?", (key,)).fetchone()
            confirmed = 1 if feedback_status == "CONFIRMED" else 0
            if row:
                support = row["support_count"] + confirmed
                total = row["total_count"] + 1
                maturity = "ESTABLISHED" if support >= PATTERN_ESTABLISHED_MIN_SUPPORT else "OBSERVING"
                db.execute("UPDATE personal_patterns SET support_count=?,total_count=?,maturity=?,last_seen_date=?,updated_at_utc=? WHERE id=?", (support, total, maturity, bundle.get("data_date"), now, row["id"]))
            else:
                maturity = "OBSERVING"
                db.execute(
                    "INSERT INTO personal_patterns (pattern_key,trigger_context_type,outcome_signal,support_count,total_count,maturity,first_seen_date,last_seen_date,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (key, trigger, signal, confirmed, 1, maturity, bundle.get("data_date"), bundle.get("data_date"), now, now),
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l6", required=True)
    parser.add_argument("--subject-type", choices=("DAILY_REASONING", "HYPOTHESIS"), required=True)
    parser.add_argument("--subject-id", type=int, required=True)
    parser.add_argument("--status", choices=("CONFIRMED", "REJECTED", "CORRECTED"), required=True)
    parser.add_argument("--correction-text", default=None)
    args = parser.parse_args()

    db = sqlite3.connect(args.l6)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        db.execute("BEGIN IMMEDIATE")
        now = utc_now()
        cursor = db.execute(
            "INSERT INTO user_feedback (subject_type,subject_id,feedback_status,correction_text,source,created_at_utc) VALUES (?,?,?,?,?,?)",
            (args.subject_type, args.subject_id, args.status, args.correction_text, "USER_FEEDBACK", now),
        )
        feedback_id = cursor.lastrowid

        bundle = None
        if args.subject_type == "DAILY_REASONING":
            row = db.execute(
                "SELECT b.bundle_json, b.analysis_date FROM evidence_bundles b JOIN daily_reasoning d ON d.evidence_bundle_id=b.id WHERE d.id=?",
                (args.subject_id,),
            ).fetchone()
            if row:
                bundle = json.loads(row["bundle_json"])
        if bundle is not None:
            update_patterns(db, bundle, args.status, now)

        corrected_ids = []
        if args.status == "CORRECTED" and args.correction_text:
            adapter = MockReasoningModelAdapter()
            events = adapter.extract_context(args.correction_text, bundle.get("analysis_date") if bundle else None)
            for event in events:
                c = db.execute(
                    "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                    (event["context_date"], event["context_type"], event.get("body_part"), event.get("severity"), args.correction_text, "USER_REPORTED", "CURRENT", now, now),
                )
                corrected_ids.append(c.lastrowid)
        db.commit()
        print(json.dumps({"status": "PASS", "feedback_id": feedback_id, "corrected_context_ids": corrected_ids}, indent=2))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
