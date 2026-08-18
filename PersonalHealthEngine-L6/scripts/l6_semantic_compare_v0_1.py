"""Semantic equivalence comparison for two Layer 6 databases.

Compares deterministic reasoning content (context, feedback, evidence bundles, hypotheses,
daily reasoning, medical reviews, patterns, model invocation metadata) while ignoring pure
runtime metadata (timestamps, autoincrement ids, durations, token counts).
"""

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def normalize_json(value):
    if value is None:
        return None
    try:
        return json.dumps(json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return value


def snapshot(path):
    db = sqlite3.connect(readonly_uri(path), uri=True)
    db.row_factory = sqlite3.Row
    try:
        context = Counter(
            (row["context_date"], row["context_type"], row["body_part"], row["severity"], row["source"], row["status"])
            for row in db.execute("SELECT * FROM personal_context ORDER BY id")
        )
        feedback = Counter(
            (row["subject_type"], row["feedback_status"], row["correction_text"], row["source"])
            for row in db.execute("SELECT * FROM user_feedback ORDER BY id")
        )
        evidence = Counter(
            (row["analysis_date"], row["bundle_sha256"], row["evidence_definition_id"], row["evidence_definition_version"], row["status"])
            for row in db.execute("SELECT * FROM evidence_bundles ORDER BY id")
        )
        hypotheses = Counter(
            (
                row["analysis_date"], row["hypothesis_type"], row["rank"], row["confidence"],
                normalize_json(row["supporting_json"]), normalize_json(row["counter_json"]),
                normalize_json(row["missing_json"]), row["reasoning_summary"], row["origin"], row["status"],
            )
            for row in db.execute("SELECT * FROM hypotheses ORDER BY id")
        )
        daily = Counter(
            (
                row["analysis_date"], row["overall_state"], row["primary_hypothesis_type"],
                row["secondary_hypothesis_type"], row["confidence"], normalize_json(row["recommended_actions_json"]),
                row["medical_review_state"], row["reasoning_model"], row["medical_model"],
                row["reasoning_summary"], row["definition_id"], row["definition_version"], row["status"],
            )
            for row in db.execute("SELECT * FROM daily_reasoning ORDER BY id")
        )
        reviews = Counter(
            (row["subject_type"], row["review_state"], row["trigger_reason"], normalize_json(row["findings_json"]), row["reviewer_model"])
            for row in db.execute("SELECT * FROM medical_reviews ORDER BY id")
        )
        patterns = Counter(
            (
                row["pattern_key"], row["trigger_context_type"], row["outcome_signal"],
                row["support_count"], row["total_count"], row["maturity"],
                row["first_seen_date"], row["last_seen_date"],
            )
            for row in db.execute("SELECT * FROM personal_patterns ORDER BY id")
        )
        invocations = Counter(
            (
                row["adapter_kind"], row["model_id"], row["request_sha256"], row["response_sha256"],
                row["status"], row["error_code"],
            )
            for row in db.execute("SELECT * FROM model_invocations ORDER BY id")
        )
        definitions = Counter(
            (row["definition_id"], row["definition_version"], row["definition_type"], row["status"], row["definition_sha256"])
            for row in db.execute("SELECT * FROM definition_registry")
        )
        migrations = Counter(
            (row["version"], row["name"], row["checksum_sha256"])
            for row in db.execute("SELECT * FROM schema_migrations")
        )
        checkpoints = Counter(
            (row["pipeline_name"], row["last_l5_analytic_id"], row["last_l3_feature_id"], row["last_l4_baseline_id"])
            for row in db.execute("SELECT * FROM processing_checkpoints")
        )
        return {
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "definitions": definitions,
            "migrations": migrations,
            "context": context,
            "feedback": feedback,
            "evidence": evidence,
            "hypotheses": hypotheses,
            "daily": daily,
            "reviews": reviews,
            "patterns": patterns,
            "invocations": invocations,
            "checkpoints": checkpoints,
        }
    finally:
        db.close()


def compare(production_path, rebuilt_path):
    production = snapshot(production_path)
    rebuilt = snapshot(rebuilt_path)
    domains = (
        "schema_version", "definitions", "migrations", "context", "feedback", "evidence",
        "hypotheses", "daily", "patterns", "invocations", "checkpoints",
    )
    checks = []
    for domain in domains:
        passed = production[domain] == rebuilt[domain]
        evidence = {
            "production_count": sum(production[domain].values()) if isinstance(production[domain], Counter) else production[domain],
            "rebuilt_count": sum(rebuilt[domain].values()) if isinstance(rebuilt[domain], Counter) else rebuilt[domain],
        }
        if not passed and isinstance(production[domain], Counter):
            evidence["production_only"] = [repr(i) for i in list((production[domain] - rebuilt[domain]).elements())[:3]]
            evidence["rebuilt_only"] = [repr(i) for i in list((rebuilt[domain] - production[domain]).elements())[:3]]
        checks.append({"domain": domain, "status": "PASS" if passed else "FAIL", "evidence": evidence})
    for label, snap in (("production", production), ("rebuilt", rebuilt)):
        checks.append({"domain": f"{label}_integrity", "status": "PASS" if snap["integrity"] == "ok" else "FAIL", "evidence": snap["integrity"]})
        checks.append({"domain": f"{label}_foreign_keys", "status": "PASS" if snap["foreign_key_errors"] == 0 else "FAIL", "evidence": snap["foreign_key_errors"]})
    passed = sum(check["status"] == "PASS" for check in checks)
    return {"status": "PASS" if passed == len(checks) else "FAIL", "checks_passed": passed, "checks_failed": len(checks) - passed, "checks_total": len(checks), "checks": checks}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--production", required=True)
    parser.add_argument("--rebuilt", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = compare(args.production, args.rebuilt)
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
