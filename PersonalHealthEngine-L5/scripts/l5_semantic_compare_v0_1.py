"""Semantic equivalence comparison for two Layer 5 databases.

Compares analytic identity, type, target feature/date, baseline identity, window,
deviation/persistence/trend/change/relationship results, evidence state, and provenance
input sets — not row counts. Used by full-rebuild acceptance.
"""

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
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
        series = {}
        for row in db.execute("SELECT * FROM analytics_series ORDER BY id"):
            series[row["id"]] = (
                row["series_key"], row["l4_series_id"], row["feature_name"], row["scope_type"],
                row["provider"], row["source_sid"], row["source_class"], row["timezone_name"],
                row["timezone_offset_seconds"], row["unit"], row["observation_semantics"],
                row["status"],
            )

        l3_inputs = defaultdict(list)
        for row in db.execute("SELECT analytic_type,analytic_id,l3_feature_id FROM analytics_l3_inputs ORDER BY l3_feature_id"):
            l3_inputs[(row["analytic_type"], row["analytic_id"])].append(row["l3_feature_id"])
        b_inputs = defaultdict(list)
        for row in db.execute("SELECT analytic_type,analytic_id,l4_baseline_id FROM analytics_baseline_inputs ORDER BY l4_baseline_id"):
            b_inputs[(row["analytic_type"], row["analytic_id"])].append(row["l4_baseline_id"])

        def ids(key):
            return tuple(sorted(l3_inputs.get(key, [])))

        def bids(key):
            return tuple(sorted(b_inputs.get(key, [])))

        deviations = Counter()
        for row in db.execute("SELECT * FROM deviation_analytics ORDER BY id"):
            key = ("DEVIATION", row["id"])
            sk = series[row["series_id"]][0]
            deviations[(
                sk, row["window_days"], row["feature_date"], row["l3_feature_id"], row["l4_baseline_id"],
                row["baseline_maturity"], row["baseline_median"], row["baseline_mad"], row["current_value"],
                row["absolute_deviation"], row["relative_deviation"], row["relative_deviation_applicable"],
                row["robust_standardized_deviation"], row["robust_z_unavailable_reason"], row["quantile_position"],
                row["deviation_side"], row["deviation_class"], row["evidence_status"],
                normalize_json(row["attributes_json"]), ids(key), bids(key),
            )] += 1

        persistence = Counter()
        for row in db.execute("SELECT * FROM persistence_analytics ORDER BY id"):
            key = ("PERSISTENCE", row["id"])
            sk = series[row["series_id"]][0]
            persistence[(
                sk, row["window_days"], row["as_of_date"], row["trailing_observation_count"],
                row["consecutive_above_typical"], row["consecutive_below_typical"], row["persistence_class"],
                row["evidence_status"], normalize_json(row["attributes_json"]), ids(key), bids(key),
            )] += 1

        trend = Counter()
        for row in db.execute("SELECT * FROM trend_analytics ORDER BY id"):
            key = ("TREND", row["id"])
            sk = series[row["series_id"]][0]
            trend[(
                sk, row["as_of_date"], row["trend_point_count"], row["trend_start_date"], row["trend_end_date"],
                row["theil_sen_slope"], row["spearman_rho"], row["trend_class"], row["evidence_status"],
                normalize_json(row["attributes_json"]), ids(key),
            )] += 1

        change = Counter()
        for row in db.execute("SELECT * FROM change_point_analytics ORDER BY id"):
            key = ("CHANGE_POINT", row["id"])
            sk = series[row["series_id"]][0]
            change[(
                sk, row["as_of_date"], row["observation_count"], row["candidate_split_date"],
                row["shift_magnitude"], row["change_class"], row["evidence_status"],
                normalize_json(row["attributes_json"]), ids(key),
            )] += 1

        relationship = Counter()
        for row in db.execute("SELECT * FROM relationship_analytics ORDER BY id"):
            key = ("RELATIONSHIP", row["id"])
            ka = series[row["series_id_a"]][0]
            kb = series[row["series_id_b"]][0]
            relationship[(
                ka, kb, row["as_of_date"], row["paired_count"], row["spearman_rho"],
                row["relationship_class"], row["evidence_status"],
                normalize_json(row["attributes_json"]), ids(key),
            )] += 1

        definitions = Counter(
            (row["definition_id"], row["definition_version"], row["definition_type"], row["status"], row["definition_sha256"])
            for row in db.execute("SELECT * FROM definition_registry")
        )
        migrations = Counter(
            (row["version"], row["name"], row["checksum_sha256"])
            for row in db.execute("SELECT * FROM schema_migrations")
        )
        checkpoints = Counter(
            (row["pipeline_name"], row["last_l3_feature_id"], row["last_l4_baseline_id"])
            for row in db.execute("SELECT * FROM processing_checkpoints")
        )
        issues = Counter(
            (row["stage"], row["series_key"], row["issue_code"], row["severity"], row["message"])
            for row in db.execute("SELECT * FROM analytics_issues")
        )
        return {
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "definitions": definitions,
            "migrations": migrations,
            "series": Counter(series.values()),
            "deviation": deviations,
            "persistence": persistence,
            "trend": trend,
            "change": change,
            "relationship": relationship,
            "checkpoints": checkpoints,
            "issues": issues,
        }
    finally:
        db.close()


def compare(production_path, rebuilt_path):
    production = snapshot(production_path)
    rebuilt = snapshot(rebuilt_path)
    domains = (
        "schema_version", "definitions", "migrations", "series", "deviation",
        "persistence", "trend", "change", "relationship", "checkpoints", "issues",
    )
    checks = []
    for domain in domains:
        passed = production[domain] == rebuilt[domain]
        evidence = {
            "production_count": sum(production[domain].values())
            if isinstance(production[domain], Counter)
            else production[domain],
            "rebuilt_count": sum(rebuilt[domain].values())
            if isinstance(rebuilt[domain], Counter)
            else rebuilt[domain],
        }
        if not passed and isinstance(production[domain], Counter):
            evidence["production_only"] = [repr(i) for i in list((production[domain] - rebuilt[domain]).elements())[:3]]
            evidence["rebuilt_only"] = [repr(i) for i in list((rebuilt[domain] - production[domain]).elements())[:3]]
        checks.append({"domain": domain, "status": "PASS" if passed else "FAIL", "evidence": evidence})
    for label, snap in (("production", production), ("rebuilt", rebuilt)):
        checks.append({"domain": f"{label}_integrity", "status": "PASS" if snap["integrity"] == "ok" else "FAIL", "evidence": snap["integrity"]})
        checks.append({"domain": f"{label}_foreign_keys", "status": "PASS" if snap["foreign_key_errors"] == 0 else "FAIL", "evidence": snap["foreign_key_errors"]})
    passed = sum(check["status"] == "PASS" for check in checks)
    return {
        "status": "PASS" if passed == len(checks) else "FAIL",
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "checks": checks,
    }


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
