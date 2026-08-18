import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


DATASETS = {
    "heart_rate": ("heart_rate", "POINT", "SENSOR_DERIVED"),
    "resting_heart_rate": (
        "resting_heart_rate",
        "DAILY",
        "VENDOR_DERIVED",
    ),
    "spo2": ("spo2", "POINT", "SENSOR_DERIVED"),
    "stress": ("xiaomi_stress_score", "POINT", "VENDOR_DERIVED"),
    "steps": ("steps", "BUCKET", "VENDOR_DERIVED"),
    "calories": ("calories", "BUCKET", "VENDOR_DERIVED"),
}


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def add(checks, check_id, name, passed, evidence):
    checks.append(
        {
            "id": check_id,
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": evidence,
        }
    )


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def current_count(db, metric):
    return scalar(
        db,
        "SELECT COUNT(*) FROM fact_registry WHERE status='CURRENT' AND metric=?",
        (metric,),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--definitions-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    l2_path = Path(args.l2).resolve()
    l3_path = Path(args.l3).resolve()
    definitions_root = Path(args.definitions_root).resolve()
    root = definitions_root.parents[1]
    l2 = sqlite3.connect(readonly_uri(l2_path), uri=True)
    l2.row_factory = sqlite3.Row
    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    checks = []
    try:
        add(
            checks,
            "L3A-01",
            "SQLite integrity",
            scalar(l3, "PRAGMA integrity_check") == "ok",
            scalar(l3, "PRAGMA integrity_check"),
        )
        foreign_keys = l3.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "L3A-02", "Foreign keys", not foreign_keys, len(foreign_keys))
        journal_mode = scalar(l3, "PRAGMA journal_mode")
        add(checks, "L3A-03", "WAL journal mode", journal_mode == "wal", journal_mode)
        schema_version = scalar(l3, "PRAGMA user_version")
        add(checks, "L3A-04", "Schema version", schema_version >= 6, schema_version)

        migrations = l3.execute(
            "SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        versions = [row["version"] for row in migrations]
        add(
            checks,
            "L3A-05",
            "Migration chain",
            versions == list(range(1, schema_version + 1)),
            versions,
        )
        migration_hashes = []
        for row in migrations:
            matches = list((root / "migrations").glob(f"{row['version']:03d}_*.sql"))
            digest = hashlib.sha256(matches[0].read_bytes()).hexdigest() if len(matches) == 1 else None
            migration_hashes.append(
                {"version": row["version"], "file_count": len(matches), "match": digest == row["checksum_sha256"]}
            )
        add(
            checks,
            "L3A-06",
            "Migration checksums",
            all(item["match"] for item in migration_hashes),
            migration_hashes,
        )
        add(
            checks,
            "L3A-07",
            "L2 and L3 isolation",
            l2_path != l3_path and l2_path.parent != l3_path.parent,
            {"l2": str(l2_path), "l3": str(l3_path), "l2_open_mode": "ro"},
        )

        registry = l3.execute(
            """
            SELECT definition_id,definition_version,definition_sha256,status
            FROM definition_registry WHERE definition_type='NORMALIZER'
            ORDER BY definition_id
            """
        ).fetchall()
        add(
            checks,
            "L3A-08",
            "Definition registry integrity",
            len(registry) == 7
            and len({(row["definition_id"], row["definition_version"]) for row in registry}) == 7
            and all(row["status"] == "ACTIVE" and row["definition_sha256"] for row in registry),
            [dict(row) for row in registry],
        )
        definition_files = {}
        for path in definitions_root.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = (
                path,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        definition_matches = []
        for row in registry:
            item = definition_files.get((row["definition_id"], row["definition_version"]))
            definition_matches.append(
                {
                    "definition_id": row["definition_id"],
                    "file": str(item[0]) if item else None,
                    "match": bool(item and item[1] == row["definition_sha256"]),
                }
            )
        add(
            checks,
            "L3A-09",
            "Definition file checksums",
            len(definition_matches) == 7 and all(x["match"] for x in definition_matches),
            definition_matches,
        )

        latest_counts = dict(
            l2.execute(
                """
                SELECT lr.dataset,COUNT(*)
                FROM logical_records lr
                WHERE lr.dataset IN ('heart_rate','resting_heart_rate','sleep','spo2','stress','steps','calories')
                GROUP BY lr.dataset
                """
            ).fetchall()
        )
        coverage = {}
        for dataset, (metric, _, _) in DATASETS.items():
            coverage[dataset] = {
                "expected": latest_counts.get(dataset, 0),
                "actual": current_count(l3, metric),
            }
        sleep_items = 0
        sleep_latest = l2.execute(
            """
            WITH latest AS (
                SELECT logical_record_id,MAX(id) raw_version_id
                FROM raw_record_versions GROUP BY logical_record_id
            )
            SELECT rv.raw_json FROM logical_records lr
            JOIN latest ON latest.logical_record_id=lr.id
            JOIN raw_record_versions rv ON rv.id=latest.raw_version_id
            WHERE lr.dataset='sleep'
            """
        ).fetchall()
        for row in sleep_latest:
            outer = json.loads(row[0])
            inner = json.loads(outer["value"]) if isinstance(outer["value"], str) else outer["value"]
            sleep_items += len(inner.get("items") or [])
        coverage["sleep_source_episode"] = {
            "expected": latest_counts.get("sleep", 0),
            "actual": current_count(l3, "sleep_source_episode"),
        }
        coverage["sleep_vendor_stage_segment"] = {
            "expected": sleep_items,
            "actual": current_count(l3, "sleep_vendor_stage_segment"),
        }
        add(
            checks,
            "L3A-10",
            "Latest logical-record coverage",
            all(x["expected"] == x["actual"] for x in coverage.values()),
            coverage,
        )

        latest_map = dict(
            l2.execute(
                "SELECT logical_record_id,MAX(id) FROM raw_record_versions GROUP BY logical_record_id"
            ).fetchall()
        )
        provenance_rows = l3.execute(
            """
            SELECT fr.id,fp.l2_logical_record_id,fp.l2_raw_version_id
            FROM fact_registry fr JOIN fact_provenance fp ON fp.fact_id=fr.id
            WHERE fr.status='CURRENT'
            """
        ).fetchall()
        parity_mismatches = [
            row["id"]
            for row in provenance_rows
            if latest_map.get(row["l2_logical_record_id"]) != row["l2_raw_version_id"]
        ]
        add(
            checks,
            "L3A-11",
            "Latest raw-version parity",
            not parity_mismatches,
            {"mismatch_count": len(parity_mismatches)},
        )
        current_total = scalar(l3, "SELECT COUNT(*) FROM fact_registry WHERE status='CURRENT'")
        provenance_groups = l3.execute(
            """
            SELECT fr.id,COUNT(fp.l2_raw_version_id) n
            FROM fact_registry fr LEFT JOIN fact_provenance fp ON fp.fact_id=fr.id
            WHERE fr.status='CURRENT' GROUP BY fr.id HAVING n<>1
            """
        ).fetchall()
        add(
            checks,
            "L3A-12",
            "Full L3A provenance",
            not provenance_groups and len(provenance_rows) == current_total,
            {"current_facts": current_total, "provenance_rows": len(provenance_rows)},
        )

        duplicates = 0
        for table, columns in {
            "normalized_point_facts": "event_time_utc,value_num,value_code,unit,source_sid,attributes_json",
            "normalized_daily_facts": "local_date,value_num,value_code,unit,source_sid,attributes_json",
            "normalized_bucket_facts": "bucket_anchor_time_utc,value_num,value_code,unit,source_sid,attributes_json",
            "normalized_interval_facts": "start_time_utc,end_time_utc,value_code,unit,source_sid,attributes_json",
        }.items():
            duplicates += scalar(
                l3,
                f"""
                SELECT COUNT(*) FROM (
                    SELECT fr.metric,fp.l2_logical_record_id,fp.l2_raw_version_id,{columns},COUNT(*) n
                    FROM fact_registry fr JOIN {table} x ON x.fact_id=fr.id
                    JOIN fact_provenance fp ON fp.fact_id=fr.id
                    WHERE fr.status='CURRENT'
                    GROUP BY fr.metric,fp.l2_logical_record_id,fp.l2_raw_version_id,{columns}
                    HAVING n>1
                )
                """,
            )
        add(checks, "L3A-13", "No duplicate CURRENT facts", duplicates == 0, duplicates)

        semantic_mismatches = []
        for _, (metric, kind, evidence) in DATASETS.items():
            count = scalar(
                l3,
                """
                SELECT COUNT(*) FROM fact_registry
                WHERE status='CURRENT' AND metric=?
                  AND (fact_kind<>? OR evidence_type<>?)
                """,
                (metric, kind, evidence),
            )
            semantic_mismatches.append({"metric": metric, "mismatches": count})
        for metric in ("sleep_source_episode", "sleep_vendor_stage_segment"):
            count = scalar(
                l3,
                """
                SELECT COUNT(*) FROM fact_registry
                WHERE status='CURRENT' AND metric=?
                  AND (fact_kind<>'INTERVAL' OR evidence_type<>'VENDOR_INFERRED')
                """,
                (metric,),
            )
            semantic_mismatches.append({"metric": metric, "mismatches": count})
        add(
            checks,
            "L3A-14",
            "Temporal types",
            all(x["mismatches"] == 0 for x in semantic_mismatches),
            semantic_mismatches,
        )
        add(
            checks,
            "L3A-15",
            "Evidence types",
            all(x["mismatches"] == 0 for x in semantic_mismatches),
            semantic_mismatches,
        )

        source_counts = {}
        for table, metrics in (
            ("normalized_bucket_facts", ("steps", "calories")),
            ("normalized_interval_facts", ("sleep_source_episode",)),
        ):
            for metric in metrics:
                source_counts[metric] = dict(
                    l3.execute(
                        f"""
                        SELECT x.source_class,COUNT(*) FROM fact_registry fr
                        JOIN {table} x ON x.fact_id=fr.id
                        WHERE fr.status='CURRENT' AND fr.metric=? GROUP BY x.source_class
                        """,
                        (metric,),
                    ).fetchall()
                )
        add(
            checks,
            "L3A-16",
            "Source coexistence",
            all(set(counts) == {"NUMERIC_SOURCE", "XIAOMI_GENERATED"} for counts in source_counts.values()),
            source_counts,
        )

        l2_timezones = {
            row["id"]: (row["zone_name"], row["zone_offset"])
            for row in l2.execute(
                "SELECT id,zone_name,zone_offset FROM raw_record_versions"
            )
        }
        timezone_mismatches = []
        for table in (
            "normalized_point_facts",
            "normalized_daily_facts",
            "normalized_bucket_facts",
            "normalized_interval_facts",
        ):
            rows = l3.execute(
                f"""
                SELECT fr.id,x.timezone_name,x.timezone_offset_seconds,
                       fp.l2_raw_version_id
                FROM fact_registry fr JOIN {table} x ON x.fact_id=fr.id
                JOIN fact_provenance fp ON fp.fact_id=fr.id
                WHERE fr.status='CURRENT'
                """,
            ).fetchall()
            for row in rows:
                actual = (row["timezone_name"], row["timezone_offset_seconds"])
                expected = l2_timezones[row["l2_raw_version_id"]]
                if actual != expected:
                    timezone_mismatches.append(
                        {"fact_id": row["id"], "actual": actual, "expected": expected}
                    )
        add(
            checks,
            "L3A-17",
            "Timezone metadata preservation",
            not timezone_mismatches,
            {"mismatch_count": len(timezone_mismatches)},
        )

        point_invalid = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr JOIN normalized_point_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT' AND x.event_time_utc IS NULL
            """,
        )
        add(checks, "L3A-18", "POINT temporal semantics", point_invalid == 0, point_invalid)
        daily_invalid = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr JOIN normalized_daily_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT' AND x.local_date IS NULL
            """,
        )
        add(checks, "L3A-19", "DAILY temporal semantics", daily_invalid == 0, daily_invalid)
        bucket_invalid = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr JOIN normalized_bucket_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT'
              AND (x.bucket_width_seconds IS NOT NULL OR x.bucket_semantics<>'VENDOR_UNRESOLVED')
            """,
        )
        add(checks, "L3A-20", "BUCKET unresolved semantics", bucket_invalid == 0, bucket_invalid)
        interval_invalid = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr JOIN normalized_interval_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT'
              AND (x.end_time_utc<x.start_time_utc OR x.duration_seconds<0)
            """,
        )
        zero_intervals = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr JOIN normalized_interval_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT' AND x.duration_seconds=0
            """,
        )
        add(
            checks,
            "L3A-21",
            "INTERVAL temporal semantics",
            interval_invalid == 0 and zero_intervals == 2,
            {"invalid": interval_invalid, "zero_duration_preserved": zero_intervals},
        )

        frontier = scalar(l2, "SELECT COALESCE(MAX(id),0) FROM raw_record_observations")
        checkpoints = dict(
            l3.execute(
                "SELECT pipeline_name,last_l2_observation_id FROM processing_checkpoints"
            ).fetchall()
        )
        required_pipelines = {f"normalize.{metric}" for metric in (
            "heart_rate", "resting_heart_rate", "sleep", "spo2", "steps", "calories"
        )} | {"normalize.xiaomi_stress_score"}
        add(
            checks,
            "L3A-22",
            "Checkpoint correctness",
            all(checkpoints.get(name) == frontier for name in required_pipelines),
            {"frontier": frontier, "checkpoints": checkpoints},
        )

        runs = []
        for row in l3.execute(
            "SELECT run_id,status,details_json FROM pipeline_runs WHERE mode='FULL_REBUILD' AND status='PASS'"
        ):
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if details.get("inserted") == 0 and details.get("superseded", 0) == 0:
                runs.append(row["run_id"])
        idempotent_prefixes = (
            "hr-full-", "spo2-full-", "xiaomi_stress_score-generic-full-",
            "resting-heart-rate-full-", "steps-full-", "calories-bucket-full-", "sleep-full-",
        )
        add(
            checks,
            "L3A-23",
            "Full normalization idempotency",
            all(any(run.startswith(prefix) for run in runs) for prefix in idempotent_prefixes),
            runs,
        )

        classification_evidence = {}
        for name, path in {
            "POINT": root / "generic_point_incremental_test" / "db" / "personal_health_features.sqlite3",
            "BUCKET": root / "bucket_incremental_acceptance_test" / "l3.sqlite3",
        }.items():
            if not path.exists():
                classification_evidence[name] = {"exists": False}
                continue
            db = sqlite3.connect(readonly_uri(path), uri=True)
            db.row_factory = sqlite3.Row
            evidence_runs = []
            for row in db.execute("SELECT details_json FROM pipeline_runs WHERE mode='INCREMENTAL' AND status='PASS'"):
                details = json.loads(row[0] or "{}")
                keys = ("target_new", "target_revision", "target_reobservation")
                if all(details.get(key) == 1 for key in keys):
                    evidence_runs.append(details)
            classification_evidence[name] = {
                "exists": True,
                "integrity": scalar(db, "PRAGMA integrity_check"),
                "matching_runs": evidence_runs,
            }
            db.close()
        add(
            checks,
            "L3A-24",
            "POINT and BUCKET classification evidence",
            all(x.get("integrity") == "ok" and x.get("matching_runs") for x in classification_evidence.values()),
            classification_evidence,
        )
        sleep_test_files = [
            root / "tests" / "test_sleep_zero_duration_v0_1.py",
            root / "tests" / "test_sleep_incremental_v0_1.py",
        ]
        sleep_tests = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "tests.test_sleep_zero_duration_v0_1",
                "tests.test_sleep_incremental_v0_1",
                "-v",
            ],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        add(
            checks,
            "L3A-25",
            "Sleep classification and production-isolation tests",
            all(path.exists() for path in sleep_test_files)
            and sleep_tests.returncode == 0,
            {
                "files": [str(path) for path in sleep_test_files],
                "returncode": sleep_tests.returncode,
                "summary": sleep_tests.stderr.splitlines()[-4:],
            },
        )
        allowed_definitions = {row["definition_id"] for row in registry}
        fact_definitions = {
            row[0] for row in l3.execute("SELECT DISTINCT definition_id FROM fact_registry")
        }
        add(
            checks,
            "L3A-26",
            "No L3B/L3C leakage into normalized facts",
            fact_definitions <= allowed_definitions,
            sorted(fact_definitions),
        )
        add(
            checks,
            "L3A-27",
            "L3A rebuild inputs present",
            len(list((root / "migrations").glob("*.sql"))) >= 6
            and len(definition_files) == 7
            and all((root / "scripts" / name).exists() for name in (
                "l3_point_full_runner_v0_1.py",
                "l3_bucket_full_runner_v0_1.py",
                "l3_sleep_full_runner_v0_1.py",
            )),
            "migrations + 7 definitions + POINT/BUCKET/INTERVAL runners",
        )
    finally:
        l2.close()
        l3.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "stage": "L3A",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "checks": checks,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("stage", "status", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
