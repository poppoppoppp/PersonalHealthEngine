import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


FACT_TABLES = (
    "normalized_point_facts",
    "normalized_daily_facts",
    "normalized_bucket_facts",
    "normalized_interval_facts",
)


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


def fact_keys(db):
    provenance = defaultdict(list)
    for row in db.execute(
        """
        SELECT fact_id,l2_logical_record_id,l2_raw_version_id,provenance_role
        FROM fact_provenance ORDER BY fact_id,l2_logical_record_id,l2_raw_version_id
        """
    ):
        provenance[row[0]].append(tuple(row[1:]))
    result = {}
    for table in FACT_TABLES:
        columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        value_columns = [column for column in columns if column != "fact_id"]
        select_values = ",".join(f"x.{column}" for column in value_columns)
        for row in db.execute(
            f"""
            SELECT fr.id,fr.fact_kind,fr.metric,fr.evidence_type,
                   fr.definition_id,fr.definition_version,fr.status,{select_values}
            FROM fact_registry fr JOIN {table} x ON x.fact_id=fr.id
            ORDER BY fr.id
            """
        ):
            values = list(row[7:])
            if "attributes_json" in value_columns:
                values[value_columns.index("attributes_json")] = normalize_json(
                    values[value_columns.index("attributes_json")]
                )
            result[row[0]] = (
                row[1],row[2],row[3],row[4],row[5],row[6],table,
                tuple(zip(value_columns, values)),
                tuple(provenance[row[0]]),
            )
    return result


def quality_keys(db, facts):
    inputs = defaultdict(list)
    for row in db.execute(
        "SELECT assessment_id,fact_id,input_role FROM quality_assessment_inputs"
    ):
        inputs[row[0]].append((facts[row[1]], row[2]))
    result = {}
    for row in db.execute("SELECT * FROM quality_assessments ORDER BY id"):
        result[row["id"]] = (
            facts[row["subject_fact_id"]],row["metric"],row["quality_dimension"],
            row["result"],row["reason_code"],row["definition_id"],
            row["definition_version"],row["status"],normalize_json(row["details_json"]),
            tuple(sorted(inputs[row["id"]], key=repr)),
        )
    return result


def resolution_keys(db, facts):
    inputs = defaultdict(list)
    for row in db.execute(
        "SELECT decision_id,fact_id,membership_role FROM source_resolution_inputs"
    ):
        inputs[row[0]].append((facts[row[1]], row[2]))
    result = {}
    for row in db.execute("SELECT * FROM source_resolution_decisions ORDER BY id"):
        result[row["id"]] = (
            row["metric"],row["component"],normalize_json(row["grouping_key"]),
            row["decision"],row["outcome"],row["reason_code"],
            row["definition_id"],row["definition_version"],row["status"],
            normalize_json(row["details_json"]),
            tuple(sorted(inputs[row["id"]], key=repr)),
        )
    return result


def feature_keys(db, facts, quality, resolutions):
    fact_inputs = defaultdict(list)
    for row in db.execute(
        "SELECT feature_id,fact_id,input_role FROM derived_feature_fact_inputs"
    ):
        fact_inputs[row[0]].append((facts[row[1]], row[2]))
    quality_inputs = defaultdict(list)
    for row in db.execute(
        "SELECT feature_id,assessment_id FROM derived_feature_quality_inputs"
    ):
        quality_inputs[row[0]].append(quality[row[1]])
    resolution_inputs = defaultdict(list)
    for row in db.execute(
        "SELECT feature_id,decision_id FROM derived_feature_resolution_inputs"
    ):
        resolution_inputs[row[0]].append(resolutions[row[1]])
    result = {}
    for row in db.execute("SELECT * FROM derived_features ORDER BY id"):
        result[row["id"]] = (
            row["feature_name"],row["scope_type"],normalize_json(row["scope_key"]),
            row["local_date"],row["value_num"],row["value_code"],row["unit"],
            row["sample_count"],row["provider"],row["source_sid"],
            row["source_class"],row["timezone_name"],
            row["timezone_offset_seconds"],row["coverage_status"],
            row["definition_id"],row["definition_version"],row["status"],
            normalize_json(row["attributes_json"]),
            tuple(sorted(fact_inputs[row["id"]], key=repr)),
            tuple(sorted(quality_inputs[row["id"]], key=repr)),
            tuple(sorted(resolution_inputs[row["id"]], key=repr)),
        )
    return result


def snapshot(path):
    db = sqlite3.connect(readonly_uri(path), uri=True)
    db.row_factory = sqlite3.Row
    try:
        facts = fact_keys(db)
        quality = quality_keys(db, facts)
        resolutions = resolution_keys(db, facts)
        features = feature_keys(db, facts, quality, resolutions)
        definitions = Counter(
            (
                row["definition_id"],row["definition_version"],
                row["definition_type"],row["status"],row["definition_sha256"],
            )
            for row in db.execute("SELECT * FROM definition_registry")
        )
        migrations = Counter(
            (row["version"],row["name"],row["checksum_sha256"])
            for row in db.execute("SELECT * FROM schema_migrations")
        )
        checkpoints = Counter(
            (row["pipeline_name"],row["last_l2_observation_id"])
            for row in db.execute("SELECT * FROM processing_checkpoints")
        )
        issues = Counter(
            (
                row["stage"],row["dataset"],row["l2_logical_record_id"],
                row["l2_raw_version_id"],row["issue_code"],row["severity"],row["message"],
            )
            for row in db.execute("SELECT * FROM normalization_issues")
        )
        return {
            "schema_version": db.execute("PRAGMA user_version").fetchone()[0],
            "integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_errors": len(db.execute("PRAGMA foreign_key_check").fetchall()),
            "definitions": definitions,
            "migrations": migrations,
            "facts": Counter(facts.values()),
            "quality": Counter(quality.values()),
            "resolutions": Counter(resolutions.values()),
            "features": Counter(features.values()),
            "checkpoints": checkpoints,
            "issues": issues,
        }
    finally:
        db.close()


def compare(production_path, rebuilt_path):
    production = snapshot(production_path)
    rebuilt = snapshot(rebuilt_path)
    domains = (
        "schema_version","definitions","migrations","facts","quality",
        "resolutions","features","checkpoints","issues",
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
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in ("status","checks_passed","checks_failed","checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
