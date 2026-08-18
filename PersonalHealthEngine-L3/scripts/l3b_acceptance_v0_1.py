import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


QUALITY_ID = "l3b.quality.structural"
RESOLUTION_ID = "l3b.resolution.source"


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--quality-definition", required=True)
    parser.add_argument("--resolution-definition", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    l2_path = Path(args.l2).resolve()
    l3_path = Path(args.l3).resolve()
    root = Path(args.quality_definition).resolve().parents[2]
    l2 = sqlite3.connect(readonly_uri(l2_path), uri=True)
    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    checks = []
    try:
        integrity = scalar(l3, "PRAGMA integrity_check")
        add(checks, "L3B-01", "SQLite integrity", integrity == "ok", integrity)
        fk_errors = l3.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "L3B-02", "Foreign keys", not fk_errors, len(fk_errors))
        schema_version = scalar(l3, "PRAGMA user_version")
        add(checks, "L3B-03", "L3B schema version", schema_version >= 7, schema_version)

        l3a_report_path = root / "L3A_ACCEPTANCE.json"
        l3a_report = (
            json.loads(l3a_report_path.read_text(encoding="utf-8"))
            if l3a_report_path.exists()
            else {}
        )
        add(
            checks,
            "L3B-04",
            "L3A acceptance preserved",
            l3a_report.get("status") == "PASS"
            and l3a_report.get("checks_failed") == 0,
            {"artifact": str(l3a_report_path), "status": l3a_report.get("status")},
        )

        current_facts = scalar(
            l3, "SELECT COUNT(*) FROM fact_registry WHERE status='CURRENT'"
        )
        stale_facts = scalar(
            l3, "SELECT COUNT(*) FROM fact_registry WHERE status<>'CURRENT'"
        )
        add(
            checks,
            "L3B-05",
            "L3A facts not overwritten or deleted",
            current_facts == 5179 and stale_facts == 0,
            {"current": current_facts, "non_current": stale_facts},
        )

        definition_evidence = []
        for definition_id, path_text in (
            (QUALITY_ID, args.quality_definition),
            (RESOLUTION_ID, args.resolution_definition),
        ):
            path = Path(path_text)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            row = l3.execute(
                """
                SELECT definition_version,definition_sha256,status
                FROM definition_registry WHERE definition_id=?
                """,
                (definition_id,),
            ).fetchone()
            definition_evidence.append(
                {
                    "definition_id": definition_id,
                    "version": row[0] if row else None,
                    "checksum_match": bool(row and row[1] == digest),
                    "status": row[2] if row else None,
                }
            )
        add(
            checks,
            "L3B-06",
            "Quality and resolution rules versioned",
            all(
                item["version"] == "0.1"
                and item["checksum_match"]
                and item["status"] == "ACTIVE"
                for item in definition_evidence
            ),
            definition_evidence,
        )

        quality_current = scalar(
            l3, "SELECT COUNT(*) FROM quality_assessments WHERE status='CURRENT'"
        )
        add(
            checks,
            "L3B-07",
            "Three explicit quality dimensions per CURRENT fact",
            quality_current == current_facts * 3,
            {"facts": current_facts, "assessments": quality_current},
        )
        dimensions = dict(
            l3.execute(
                """
                SELECT quality_dimension,COUNT(*) FROM quality_assessments
                WHERE status='CURRENT' GROUP BY quality_dimension
                """
            ).fetchall()
        )
        expected_dimensions = {
            "STRUCTURAL_VALIDITY",
            "PROVENANCE_COMPLETENESS",
            "VENDOR_SEMANTIC_CERTAINTY",
        }
        add(
            checks,
            "L3B-08",
            "Quality dimensions are transparent",
            set(dimensions) == expected_dimensions
            and all(count == current_facts for count in dimensions.values()),
            dimensions,
        )
        quality_input_missing = len(
            l3.execute(
                """
                SELECT qa.id FROM quality_assessments qa
                LEFT JOIN quality_assessment_inputs i ON i.assessment_id=qa.id
                WHERE qa.status='CURRENT'
                GROUP BY qa.id HAVING COUNT(i.fact_id)<>1
                """
            ).fetchall()
        )
        add(
            checks,
            "L3B-09",
            "Quality provenance",
            quality_input_missing == 0,
            quality_input_missing,
        )
        stale_subjects = scalar(
            l3,
            """
            SELECT COUNT(*) FROM quality_assessments qa
            JOIN fact_registry fr ON fr.id=qa.subject_fact_id
            WHERE qa.status='CURRENT' AND fr.status<>'CURRENT'
            """,
        )
        add(
            checks,
            "L3B-10",
            "Quality results depend only on CURRENT facts",
            stale_subjects == 0,
            stale_subjects,
        )
        vendor_bad = scalar(
            l3,
            """
            SELECT COUNT(*) FROM quality_assessments qa
            JOIN fact_registry fr ON fr.id=qa.subject_fact_id
            WHERE qa.status='CURRENT'
              AND qa.quality_dimension='VENDOR_SEMANTIC_CERTAINTY'
              AND fr.evidence_type IN ('VENDOR_DERIVED','VENDOR_INFERRED')
              AND qa.result<>'UNKNOWN'
            """,
        )
        add(
            checks,
            "L3B-11",
            "Vendor uncertainty is not physiological truth",
            vendor_bad == 0,
            vendor_bad,
        )

        decisions = scalar(
            l3,
            "SELECT COUNT(*) FROM source_resolution_decisions WHERE status='CURRENT'",
        )
        decisions_without_inputs = len(
            l3.execute(
                """
                SELECT d.id FROM source_resolution_decisions d
                LEFT JOIN source_resolution_inputs i ON i.decision_id=d.id
                WHERE d.status='CURRENT' GROUP BY d.id HAVING COUNT(i.fact_id)=0
                """
            ).fetchall()
        )
        add(
            checks,
            "L3B-12",
            "Resolution provenance",
            decisions > 0 and decisions_without_inputs == 0,
            {"decisions": decisions, "without_inputs": decisions_without_inputs},
        )
        membership_mismatch = len(
            l3.execute(
                """
                SELECT fr.id FROM fact_registry fr
                LEFT JOIN source_resolution_inputs i ON i.fact_id=fr.id
                LEFT JOIN source_resolution_decisions d
                  ON d.id=i.decision_id AND d.status='CURRENT'
                WHERE fr.status='CURRENT'
                GROUP BY fr.id HAVING COUNT(d.id)<>1
                """
            ).fetchall()
        )
        add(
            checks,
            "L3B-13",
            "Every CURRENT fact has one resolution membership",
            membership_mismatch == 0,
            membership_mismatch,
        )

        source_counts = {}
        for table, metric in (
            ("normalized_bucket_facts", "steps"),
            ("normalized_bucket_facts", "calories"),
            ("normalized_interval_facts", "sleep_source_episode"),
        ):
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
            "L3B-14",
            "Source coexistence preserved",
            all(set(counts) == {"NUMERIC_SOURCE", "XIAOMI_GENERATED"} for counts in source_counts.values()),
            source_counts,
        )
        unresolved = scalar(
            l3,
            """
            SELECT COUNT(*) FROM source_resolution_decisions
            WHERE status='CURRENT' AND decision='CONFLICT' AND outcome='UNRESOLVED'
            """,
        )
        add(
            checks,
            "L3B-15",
            "Unresolved conflicts preserved",
            unresolved > 0,
            unresolved,
        )
        rejected = scalar(
            l3,
            "SELECT COUNT(*) FROM source_resolution_decisions WHERE status='CURRENT' AND outcome='REJECTED'",
        )
        add(
            checks,
            "L3B-16",
            "No source destruction",
            rejected == 0,
            rejected,
        )
        sleep_night_outputs = scalar(
            l3,
            """
            SELECT COUNT(*) FROM source_resolution_decisions
            WHERE status='CURRENT' AND (
                lower(metric) LIKE '%canonical%'
                OR lower(component) LIKE '%night%'
                OR lower(component) LIKE '%session%'
            )
            """,
        )
        add(
            checks,
            "L3B-17",
            "No incorrect Sleep night merge",
            sleep_night_outputs == 0,
            sleep_night_outputs,
        )

        latest_l3b_run = l3.execute(
            """
            SELECT details_json FROM pipeline_runs
            WHERE run_id LIKE 'l3b-full-%' AND status='PASS'
            ORDER BY started_at_utc DESC,run_id DESC LIMIT 1
            """
        ).fetchone()
        run_details = json.loads(latest_l3b_run[0]) if latest_l3b_run else {}
        add(
            checks,
            "L3B-18",
            "Deterministic full idempotency",
            run_details.get("quality_inserted") == 0
            and run_details.get("resolution_inserted") == 0
            and run_details.get("quality_stale") == 0
            and run_details.get("resolution_stale") == 0,
            run_details,
        )

        frontier = scalar(l2, "SELECT COALESCE(MAX(id),0) FROM raw_record_observations")
        checkpoint = l3.execute(
            "SELECT last_l2_observation_id FROM processing_checkpoints WHERE pipeline_name='l3b.quality_resolution'"
        ).fetchone()
        add(
            checks,
            "L3B-19",
            "Checkpoint correctness",
            bool(checkpoint and checkpoint[0] == frontier),
            {"frontier": frontier, "checkpoint": checkpoint[0] if checkpoint else None},
        )

        regression = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_l3b_materializer_v0_1", "-v"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        add(
            checks,
            "L3B-20",
            "Revision, REOBSERVATION, full/incremental regression",
            regression.returncode == 0,
            {"returncode": regression.returncode, "summary": regression.stderr.splitlines()[-4:]},
        )

        forbidden = ("diagnosis", "disease", "health_score", "risk_score", "baseline", "anomaly")
        definition_text = (
            Path(args.quality_definition).read_text(encoding="utf-8").lower()
            + Path(args.resolution_definition).read_text(encoding="utf-8").lower()
        )
        found = [term for term in forbidden if term in definition_text]
        add(
            checks,
            "L3B-21",
            "No clinical, baseline, anomaly, or opaque-score leakage",
            not found,
            found,
        )
        add(
            checks,
            "L3B-22",
            "Quality and clinical meaning separated",
            scalar(
                l3,
                "SELECT COUNT(*) FROM quality_assessments WHERE result NOT IN ('PASS','FLAGGED','UNKNOWN')",
            ) == 0,
            "Only explicit evidence-state results are stored",
        )
    finally:
        l2.close()
        l3.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "stage": "L3B",
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
