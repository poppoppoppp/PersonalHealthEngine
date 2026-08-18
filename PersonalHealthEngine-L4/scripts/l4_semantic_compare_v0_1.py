"""Semantic equivalence comparison for two Layer 4 databases.

Compares series identity, as-of date, window, statistics, maturity, and
provenance inputs — not row counts. Used by the full-rebuild acceptance to prove
that a from-scratch rebuild matches production.
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
        return json.dumps(
            json.loads(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (json.JSONDecodeError, TypeError):
        return value


def series_keys(db):
    result = {}
    for row in db.execute("SELECT * FROM baseline_series ORDER BY id"):
        result[row["id"]] = (
            row["series_key"],
            row["feature_name"],
            row["scope_type"],
            row["provider"],
            row["source_sid"],
            row["source_class"],
            row["timezone_name"],
            row["timezone_offset_seconds"],
            row["unit"],
            row["observation_semantics"],
            row["definition_id"],
            row["definition_version"],
            row["status"],
        )
    return result


def baseline_keys(db, series):
    inputs = defaultdict(list)
    for row in db.execute(
        "SELECT baseline_id,l3_feature_id FROM baseline_feature_inputs ORDER BY baseline_id,l3_feature_id"
    ):
        inputs[row["baseline_id"]].append(row["l3_feature_id"])
    result = {}
    for row in db.execute("SELECT * FROM rolling_baselines ORDER BY id"):
        result[row["id"]] = (
            series[row["series_id"]][0],
            row["window_days"],
            row["as_of_date"],
            row["observation_count"],
            row["distinct_observation_dates"],
            row["history_span_days"],
            row["calendar_coverage"],
            row["mean"],
            row["median"],
            row["mad"],
            row["q10"],
            row["q25"],
            row["q50"],
            row["q75"],
            row["q90"],
            row["unit"],
            row["maturity"],
            row["maturity_definition_id"],
            row["maturity_definition_version"],
            row["window_definition_id"],
            row["window_definition_version"],
            normalize_json(row["attributes_json"]),
            tuple(sorted(inputs[row["id"]])),
        )
    return result


def snapshot(path):
    db = sqlite3.connect(readonly_uri(path), uri=True)
    db.row_factory = sqlite3.Row
    try:
        series = series_keys(db)
        baselines = baseline_keys(db, series)
        definitions = Counter(
            (
                row["definition_id"], row["definition_version"],
                row["definition_type"], row["status"], row["definition_sha256"],
            )
            for row in db.execute("SELECT * FROM definition_registry")
        )
        migrations = Counter(
            (row["version"], row["name"], row["checksum_sha256"])
            for row in db.execute("SELECT * FROM schema_migrations")
        )
        checkpoints = Counter(
            (row["pipeline_name"], row["last_l3_feature_id"])
            for row in db.execute("SELECT * FROM processing_checkpoints")
        )
        issues = Counter(
            (
                row["stage"], row["series_key"], row["issue_code"],
                row["severity"], row["message"],
            )
            for row in db.execute("SELECT * FROM baseline_issues")
        )
        return {
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "definitions": definitions,
            "migrations": migrations,
            "series": Counter(series.values()),
            "baselines": Counter(baselines.values()),
            "checkpoints": checkpoints,
            "issues": issues,
        }
    finally:
        db.close()


def compare(production_path, rebuilt_path):
    production = snapshot(production_path)
    rebuilt = snapshot(rebuilt_path)
    domains = (
        "schema_version", "definitions", "migrations", "series",
        "baselines", "checkpoints", "issues",
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
            evidence["production_only"] = [
                repr(item) for item in list((production[domain] - rebuilt[domain]).elements())[:3]
            ]
            evidence["rebuilt_only"] = [
                repr(item) for item in list((rebuilt[domain] - production[domain]).elements())[:3]
            ]
        checks.append(
            {"domain": domain, "status": "PASS" if passed else "FAIL", "evidence": evidence}
        )
    for label, snap in (("production", production), ("rebuilt", rebuilt)):
        checks.append(
            {
                "domain": f"{label}_integrity",
                "status": "PASS" if snap["integrity"] == "ok" else "FAIL",
                "evidence": snap["integrity"],
            }
        )
        checks.append(
            {
                "domain": f"{label}_foreign_keys",
                "status": "PASS" if snap["foreign_key_errors"] == 0 else "FAIL",
                "evidence": snap["foreign_key_errors"],
            }
        )
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
