import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFINITION_ID = "l3c.features.daily"


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def add(checks, check_id, name, passed, evidence):
    checks.append(
        {
            "id": check_id,
            "name": name,
            "status": "PASS" if passed else "FAIL",
            "evidence": evidence,
        }
    )


def dependency_gaps(db, table, key_column):
    return len(
        db.execute(
            f"""
            SELECT f.id FROM derived_features f
            LEFT JOIN {table} i ON i.feature_id=f.id
            WHERE f.status='CURRENT'
            GROUP BY f.id HAVING COUNT(i.{key_column})=0
            """
        ).fetchall()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--definition", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    l2_path = Path(args.l2).resolve()
    l3_path = Path(args.l3).resolve()
    definition_path = Path(args.definition).resolve()
    root = definition_path.parents[2]
    l2 = sqlite3.connect(readonly_uri(l2_path), uri=True)
    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    checks = []
    try:
        integrity = scalar(l3, "PRAGMA integrity_check")
        add(checks, "L3C-01", "SQLite integrity", integrity == "ok", integrity)
        fk_errors = l3.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "L3C-02", "Foreign keys", not fk_errors, len(fk_errors))
        schema_version = scalar(l3, "PRAGMA user_version")
        add(checks, "L3C-03", "L3C schema version", schema_version >= 8, schema_version)

        for check_id, stage in (("L3C-04", "L3A"), ("L3C-05", "L3B")):
            path = root / f"{stage}_ACCEPTANCE.json"
            report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            add(
                checks,
                check_id,
                f"{stage} acceptance preserved",
                report.get("status") == "PASS" and report.get("checks_failed") == 0,
                {"artifact": str(path), "status": report.get("status")},
            )

        definition_raw = definition_path.read_bytes()
        definition = json.loads(definition_raw.decode("utf-8-sig"))
        registry = l3.execute(
            """
            SELECT definition_version,definition_sha256,status,definition_type
            FROM definition_registry WHERE definition_id=?
            """,
            (DEFINITION_ID,),
        ).fetchone()
        digest = hashlib.sha256(definition_raw).hexdigest()
        add(
            checks,
            "L3C-06",
            "Feature definition version and checksum",
            bool(
                registry
                and registry[0] == "0.1"
                and registry[1] == digest
                and registry[2] == "ACTIVE"
                and registry[3] == "FEATURE"
            ),
            {
                "definition_id": DEFINITION_ID,
                "version": registry[0] if registry else None,
                "checksum_match": bool(registry and registry[1] == digest),
            },
        )

        features = l3.execute(
            "SELECT * FROM derived_features WHERE status='CURRENT' ORDER BY id"
        ).fetchall()
        feature_count = len(features)
        names = Counter(row["feature_name"] for row in features)
        required_names = {
            "heart_rate.daily.count",
            "heart_rate.daily.mean",
            "spo2.daily.mean",
            "xiaomi_stress_score.daily.mean",
            "resting_heart_rate.daily.value",
            "steps.daily.sum",
            "calories.daily.sum",
            "sleep_source_episode.duration_seconds",
            "sleep_source_episode.vendor_stage_segment_count",
        }
        add(
            checks,
            "L3C-07",
            "Conservative feature catalog materialized",
            feature_count > 0 and required_names <= set(names),
            {"current_features": feature_count, "feature_names": dict(names)},
        )
        duplicates = len(
            l3.execute(
                """
                SELECT feature_name,scope_type,scope_key,definition_id,
                       definition_version,COUNT(*) n
                FROM derived_features WHERE status='CURRENT'
                GROUP BY feature_name,scope_type,scope_key,definition_id,
                         definition_version HAVING n>1
                """
            ).fetchall()
        )
        add(
            checks,
            "L3C-08",
            "No duplicate CURRENT features",
            duplicates == 0,
            duplicates,
        )

        fact_gaps = dependency_gaps(l3, "derived_feature_fact_inputs", "fact_id")
        quality_gaps = dependency_gaps(
            l3, "derived_feature_quality_inputs", "assessment_id"
        )
        resolution_gaps = dependency_gaps(
            l3, "derived_feature_resolution_inputs", "decision_id"
        )
        add(
            checks,
            "L3C-09",
            "Feature provenance complete",
            (fact_gaps, quality_gaps, resolution_gaps) == (0, 0, 0),
            {
                "fact_gaps": fact_gaps,
                "quality_gaps": quality_gaps,
                "resolution_gaps": resolution_gaps,
            },
        )
        multi_fact = scalar(
            l3,
            """
            SELECT COUNT(*) FROM (
                SELECT feature_id FROM derived_feature_fact_inputs
                GROUP BY feature_id HAVING COUNT(DISTINCT fact_id)>1
            )
            """,
        )
        add(
            checks,
            "L3C-10",
            "N:M fact provenance exercised",
            multi_fact > 0,
            multi_fact,
        )
        stale_fact_links = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features f
            JOIN derived_feature_fact_inputs i ON i.feature_id=f.id
            JOIN fact_registry fr ON fr.id=i.fact_id
            WHERE f.status='CURRENT' AND fr.status<>'CURRENT'
            """,
        )
        add(
            checks,
            "L3C-11",
            "CURRENT features use CURRENT facts",
            stale_fact_links == 0,
            stale_fact_links,
        )
        bad_quality_links = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features f
            JOIN derived_feature_quality_inputs i ON i.feature_id=f.id
            JOIN quality_assessments q ON q.id=i.assessment_id
            WHERE f.status='CURRENT' AND (
                q.status<>'CURRENT'
                OR (
                    q.quality_dimension IN ('STRUCTURAL_VALIDITY','PROVENANCE_COMPLETENESS')
                    AND q.result='FLAGGED'
                )
            )
            """,
        )
        add(
            checks,
            "L3C-12",
            "Quality-aware aggregation",
            bad_quality_links == 0,
            bad_quality_links,
        )
        stale_resolution_links = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features f
            JOIN derived_feature_resolution_inputs i ON i.feature_id=f.id
            JOIN source_resolution_decisions d ON d.id=i.decision_id
            WHERE f.status='CURRENT' AND d.status<>'CURRENT'
            """,
        )
        add(
            checks,
            "L3C-13",
            "Resolution-aware aggregation",
            stale_resolution_links == 0,
            stale_resolution_links,
        )

        mixed_bucket_source = scalar(
            l3,
            """
            SELECT COUNT(*) FROM (
                SELECT f.id,COUNT(DISTINCT x.source_class || ':' || x.source_sid) n
                FROM derived_features f
                JOIN derived_feature_fact_inputs i ON i.feature_id=f.id
                JOIN normalized_bucket_facts x ON x.fact_id=i.fact_id
                WHERE f.status='CURRENT' AND f.feature_name IN (
                    'steps.daily.sum','calories.daily.sum'
                ) GROUP BY f.id HAVING n>1
            )
            """,
        )
        add(
            checks,
            "L3C-14",
            "Source-aware bucket aggregation",
            mixed_bucket_source == 0,
            mixed_bucket_source,
        )
        bad_bucket_coverage = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features
            WHERE status='CURRENT'
              AND feature_name IN (
                  'steps.daily.sum','steps.daily.bucket_count',
                  'calories.daily.sum','calories.daily.bucket_count'
              )
              AND coverage_status<>'VENDOR_BUCKET_WIDTH_UNRESOLVED'
            """,
        )
        add(
            checks,
            "L3C-15",
            "Bucket coverage uncertainty preserved",
            bad_bucket_coverage == 0,
            bad_bucket_coverage,
        )
        calorie_units = {
            row[0]
            for row in l3.execute(
                """
                SELECT DISTINCT unit FROM derived_features
                WHERE status='CURRENT' AND feature_name='calories.daily.sum'
                """
            )
        }
        add(
            checks,
            "L3C-16",
            "Calories vendor unit preserved",
            calorie_units == {"vendor_calories"},
            sorted(calorie_units),
        )

        sample_errors = scalar(
            l3,
            "SELECT COUNT(*) FROM derived_features WHERE status='CURRENT' AND sample_count<1",
        )
        add(
            checks,
            "L3C-17",
            "Missing is not represented by zero samples",
            sample_errors == 0
            and definition.get("missing_rule", "").startswith("Missing groups produce no feature row"),
            {"invalid_sample_count": sample_errors, "rule": definition.get("missing_rule")},
        )
        timezone_mismatch = 0
        for feature in features:
            scope = json.loads(feature["scope_key"])
            if scope.get("local_date") != feature["local_date"]:
                timezone_mismatch += 1
            if scope.get("timezone_name") != feature["timezone_name"]:
                timezone_mismatch += 1
            if scope.get("timezone_offset_seconds") != feature["timezone_offset_seconds"]:
                timezone_mismatch += 1
        add(
            checks,
            "L3C-18",
            "Timezone and local-date semantics",
            timezone_mismatch == 0,
            timezone_mismatch,
        )

        sleep_duration_count = names.get("sleep_source_episode.duration_seconds", 0)
        sleep_scope_errors = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features WHERE status='CURRENT'
              AND feature_name LIKE 'sleep_source_episode.%'
              AND (scope_type<>'SOURCE_EPISODE' OR coverage_status<>'VENDOR_INFERENCE')
            """,
        )
        add(
            checks,
            "L3C-19",
            "Sleep remains source-episode scoped vendor inference",
            sleep_duration_count == 14 and sleep_scope_errors == 0,
            {"episode_duration_features": sleep_duration_count, "scope_errors": sleep_scope_errors},
        )
        canonical_sleep = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features WHERE status='CURRENT'
              AND (lower(feature_name) LIKE '%canonical%'
                   OR lower(feature_name) LIKE '%true_sleep%'
                   OR lower(scope_type) LIKE '%night%')
            """,
        )
        add(
            checks,
            "L3C-20",
            "No canonical night or physiological truth invented",
            canonical_sleep == 0,
            canonical_sleep,
        )

        forbidden_names = {
            "health_score",
            "readiness_score",
            "recovery_score",
            "sleep_score",
            "overall_wellness_score",
        }
        add(
            checks,
            "L3C-21",
            "No opaque composite score",
            not (forbidden_names & set(names)) and definition.get("opaque_score") is False,
            sorted(forbidden_names & set(names)),
        )
        add(
            checks,
            "L3C-22",
            "No baseline, anomaly, or clinical inference",
            definition.get("baseline") is False
            and definition.get("anomaly_detection") is False
            and definition.get("clinical_inference") is False,
            {
                "baseline": definition.get("baseline"),
                "anomaly_detection": definition.get("anomaly_detection"),
                "clinical_inference": definition.get("clinical_inference"),
            },
        )

        latest_run = l3.execute(
            """
            SELECT details_json FROM pipeline_runs
            WHERE run_id LIKE 'l3c-full-%' AND status='PASS'
            ORDER BY started_at_utc DESC,run_id DESC LIMIT 1
            """
        ).fetchone()
        run_details = json.loads(latest_run[0]) if latest_run else {}
        add(
            checks,
            "L3C-23",
            "Deterministic full idempotency",
            run_details.get("inserted") == 0 and run_details.get("stale") == 0,
            run_details,
        )
        frontier = scalar(l2, "SELECT COALESCE(MAX(id),0) FROM raw_record_observations")
        checkpoint = l3.execute(
            "SELECT last_l2_observation_id FROM processing_checkpoints WHERE pipeline_name='l3c.derived_features'"
        ).fetchone()
        add(
            checks,
            "L3C-24",
            "Checkpoint correctness",
            bool(checkpoint and checkpoint[0] == frontier),
            {"frontier": frontier, "checkpoint": checkpoint[0] if checkpoint else None},
        )

        regression = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_l3c_materializer_v0_1", "-v"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        add(
            checks,
            "L3C-25",
            "Revision, REOBSERVATION, and full/incremental equivalence",
            regression.returncode == 0,
            {"returncode": regression.returncode, "summary": regression.stderr.splitlines()[-4:]},
        )
    finally:
        l2.close()
        l3.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "stage": "L3C",
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
