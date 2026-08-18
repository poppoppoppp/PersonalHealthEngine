import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def load_report(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def artifact_check(report, check_id):
    return next((item for item in report.get("checks", []) if item.get("id") == check_id), {})


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
    parser.add_argument("--root", required=True)
    parser.add_argument("--l2", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l2_path = Path(args.l2).resolve()
    l3_path = Path(args.l3).resolve()
    l3a = load_report(root / "L3A_ACCEPTANCE.json")
    l3b = load_report(root / "L3B_ACCEPTANCE.json")
    l3c = load_report(root / "L3C_ACCEPTANCE.json")
    rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
    semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")

    l2 = sqlite3.connect(readonly_uri(l2_path), uri=True)
    l2.row_factory = sqlite3.Row
    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    checks = []
    try:
        integrity = scalar(l3, "PRAGMA integrity_check")
        add(checks, "FINAL-01", "SQLite integrity", integrity == "ok", integrity)
        fk_errors = l3.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-02", "Foreign keys", not fk_errors, len(fk_errors))
        journal = scalar(l3, "PRAGMA journal_mode")
        add(checks, "FINAL-03", "WAL journal mode", journal == "wal", journal)
        add(
            checks,
            "FINAL-04",
            "L2/L3 isolation",
            l2_path != l3_path and l2_path.parent != l3_path.parent,
            {"l2": str(l2_path), "l3": str(l3_path), "l2_mode": "ro"},
        )
        schema_version = scalar(l3, "PRAGMA user_version")
        add(checks, "FINAL-05", "Production schema version", schema_version == 8, schema_version)

        migrations = l3.execute(
            "SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        migration_versions = [row["version"] for row in migrations]
        add(
            checks,
            "FINAL-06",
            "Migration chain",
            migration_versions == list(range(1, 9)),
            migration_versions,
        )
        migration_matches = []
        for row in migrations:
            paths = list((root / "migrations").glob(f"{row['version']:03d}_*.sql"))
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths) == 1 else None
            migration_matches.append(digest == row["checksum_sha256"])
        add(
            checks,
            "FINAL-07",
            "Migration checksums",
            all(migration_matches),
            {"matched": sum(migration_matches), "total": len(migration_matches)},
        )
        migration_step = next(
            (step for step in rebuild["steps"] if step["name"] == "migrations"), {}
        )
        add(
            checks,
            "FINAL-08",
            "Migration atomicity from empty database",
            migration_step.get("status") == "PASS",
            migration_step,
        )

        definition_files = {}
        for path in (root / "definitions").rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = (
                path,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        registry = l3.execute(
            """
            SELECT definition_id,definition_version,definition_type,status,
                   definition_sha256 FROM definition_registry
            """
        ).fetchall()
        registry_matches = []
        for row in registry:
            item = definition_files.get((row["definition_id"], row["definition_version"]))
            registry_matches.append(
                bool(item and item[1] == row["definition_sha256"] and row["status"] == "ACTIVE")
            )
        add(
            checks,
            "FINAL-09",
            "Definition registry integrity",
            len(registry) == 10 and all(registry_matches),
            {"registered": len(registry), "files": len(definition_files), "matched": sum(registry_matches)},
        )

        for number, metric in enumerate(
            (
                "heart_rate",
                "resting_heart_rate",
                "sleep",
                "spo2",
                "xiaomi_stress_score",
                "steps",
                "calories",
            ),
            start=10,
        ):
            coverage_check = artifact_check(l3a, "L3A-10")
            coverage = coverage_check.get("evidence", {})
            if metric == "sleep":
                evidence = {
                    "episodes": coverage.get("sleep_source_episode"),
                    "segments": coverage.get("sleep_vendor_stage_segment"),
                }
                passed = all(
                    item and item["expected"] == item["actual"]
                    for item in evidence.values()
                )
            else:
                dataset_key = "stress" if metric == "xiaomi_stress_score" else metric
                evidence = coverage.get(dataset_key)
                passed = bool(evidence and evidence["expected"] == evidence["actual"])
            add(checks, f"FINAL-{number:02d}", f"{metric} coverage", passed, evidence)

        add(
            checks,
            "FINAL-17",
            "POINT temporal semantics",
            artifact_check(l3a, "L3A-18").get("status") == "PASS",
            artifact_check(l3a, "L3A-18").get("evidence"),
        )
        add(
            checks,
            "FINAL-18",
            "DAILY temporal semantics",
            artifact_check(l3a, "L3A-19").get("status") == "PASS",
            artifact_check(l3a, "L3A-19").get("evidence"),
        )
        add(
            checks,
            "FINAL-19",
            "BUCKET temporal semantics",
            artifact_check(l3a, "L3A-20").get("status") == "PASS",
            artifact_check(l3a, "L3A-20").get("evidence"),
        )
        add(
            checks,
            "FINAL-20",
            "INTERVAL temporal semantics",
            artifact_check(l3a, "L3A-21").get("status") == "PASS",
            artifact_check(l3a, "L3A-21").get("evidence"),
        )
        add(
            checks,
            "FINAL-21",
            "Evidence semantics",
            artifact_check(l3a, "L3A-15").get("status") == "PASS",
            artifact_check(l3a, "L3A-15").get("evidence"),
        )
        add(
            checks,
            "FINAL-22",
            "Full L3A provenance",
            artifact_check(l3a, "L3A-12").get("status") == "PASS",
            artifact_check(l3a, "L3A-12").get("evidence"),
        )
        add(
            checks,
            "FINAL-23",
            "Source coexistence",
            artifact_check(l3a, "L3A-16").get("status") == "PASS",
            artifact_check(l3a, "L3A-16").get("evidence"),
        )
        add(
            checks,
            "FINAL-24",
            "Latest raw-version parity",
            artifact_check(l3a, "L3A-11").get("status") == "PASS",
            artifact_check(l3a, "L3A-11").get("evidence"),
        )
        add(
            checks,
            "FINAL-25",
            "No duplicate CURRENT facts",
            artifact_check(l3a, "L3A-13").get("status") == "PASS",
            artifact_check(l3a, "L3A-13").get("evidence"),
        )

        for check_id, name, source_id in (
            ("FINAL-26", "L3B quality provenance", "L3B-09"),
            ("FINAL-27", "L3B resolution provenance", "L3B-12"),
            ("FINAL-28", "Unresolved preservation", "L3B-15"),
            ("FINAL-29", "No source destruction", "L3B-16"),
            ("FINAL-30", "Quality/clinical separation", "L3B-22"),
        ):
            item = artifact_check(l3b, source_id)
            add(checks, check_id, name, item.get("status") == "PASS", item.get("evidence"))

        for check_id, name, source_id in (
            ("FINAL-31", "L3C definition provenance", "L3C-06"),
            ("FINAL-32", "L3C N:M provenance", "L3C-10"),
            ("FINAL-33", "Timezone semantics", "L3C-18"),
            ("FINAL-34", "Missing is not zero", "L3C-17"),
            ("FINAL-35", "Source-aware aggregation", "L3C-14"),
        ):
            item = artifact_check(l3c, source_id)
            add(checks, check_id, name, item.get("status") == "PASS", item.get("evidence"))

        point_bucket_evidence = artifact_check(l3a, "L3A-24")
        sleep_evidence = artifact_check(l3a, "L3A-25")
        add(
            checks,
            "FINAL-36",
            "NEW propagation",
            point_bucket_evidence.get("status") == "PASS"
            and sleep_evidence.get("status") == "PASS",
            {"point_bucket": point_bucket_evidence.get("evidence"), "sleep": sleep_evidence.get("evidence")},
        )
        propagation_b = artifact_check(l3b, "L3B-20")
        propagation_c = artifact_check(l3c, "L3C-25")
        add(
            checks,
            "FINAL-37",
            "REVISION propagation",
            propagation_b.get("status") == "PASS" and propagation_c.get("status") == "PASS",
            {"l3b": propagation_b.get("evidence"), "l3c": propagation_c.get("evidence")},
        )
        add(
            checks,
            "FINAL-38",
            "REOBSERVATION no-op",
            propagation_b.get("status") == "PASS" and propagation_c.get("status") == "PASS",
            {"l3b": propagation_b.get("evidence"), "l3c": propagation_c.get("evidence")},
        )
        frontier = scalar(l2, "SELECT COALESCE(MAX(id),0) FROM raw_record_observations")
        checkpoint_rows = l3.execute(
            "SELECT pipeline_name,last_l2_observation_id FROM processing_checkpoints"
        ).fetchall()
        add(
            checks,
            "FINAL-39",
            "Checkpoint correctness",
            len(checkpoint_rows) == 9 and all(row[1] == frontier for row in checkpoint_rows),
            {"frontier": frontier, "checkpoints": [tuple(row) for row in checkpoint_rows]},
        )

        semantic_domains = {item["domain"]: item for item in semantic["checks"]}
        add(
            checks,
            "FINAL-40",
            "L3A deterministic rebuild",
            semantic_domains.get("facts", {}).get("status") == "PASS",
            semantic_domains.get("facts"),
        )
        add(
            checks,
            "FINAL-41",
            "L3B deterministic rebuild",
            all(semantic_domains.get(name, {}).get("status") == "PASS" for name in ("quality", "resolutions")),
            {name: semantic_domains.get(name) for name in ("quality", "resolutions")},
        )
        add(
            checks,
            "FINAL-42",
            "L3C deterministic rebuild",
            semantic_domains.get("features", {}).get("status") == "PASS",
            semantic_domains.get("features"),
        )
        add(
            checks,
            "FINAL-43",
            "Full Layer 3 rebuild",
            rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0,
            {"status": rebuild.get("status"), "checks": f"{rebuild.get('checks_passed')}/{rebuild.get('checks_total')}"},
        )
        add(
            checks,
            "FINAL-44",
            "Incremental/full equivalence",
            semantic.get("status") == "PASS"
            and propagation_b.get("status") == "PASS"
            and propagation_c.get("status") == "PASS",
            {"semantic": semantic.get("status"), "l3b": propagation_b.get("status"), "l3c": propagation_c.get("status")},
        )
        isolation_checks = {
            item["name"]: item["status"] for item in rebuild.get("checks", [])
        }
        add(
            checks,
            "FINAL-45",
            "Synthetic/full-rebuild production isolation",
            isolation_checks.get("production_l2_unchanged") == "PASS"
            and isolation_checks.get("production_l3_unchanged") == "PASS"
            and sleep_evidence.get("status") == "PASS",
            isolation_checks,
        )

        output_names = [
            row[0].lower()
            for row in l3.execute(
                """
                SELECT metric FROM fact_registry
                UNION ALL SELECT feature_name FROM derived_features
                UNION ALL SELECT definition_id FROM definition_registry
                """
            )
        ]
        add(
            checks,
            "FINAL-46",
            "No Layer 4 baseline leakage",
            not any("baseline" in name for name in output_names),
            "No output identifier contains baseline",
        )
        add(
            checks,
            "FINAL-47",
            "No Layer 5 anomaly-analysis leakage",
            not any("anomaly" in name or "risk_score" in name for name in output_names),
            "No output identifier contains anomaly/risk_score",
        )
        add(
            checks,
            "FINAL-48",
            "No Layer 6 AI-reasoning leakage",
            not any("ai_reason" in name or "recommendation" in name for name in output_names),
            "No output identifier contains AI reasoning/recommendation",
        )
        add(
            checks,
            "FINAL-49",
            "Final SQLite integrity",
            scalar(l3, "PRAGMA integrity_check") == "ok",
            scalar(l3, "PRAGMA integrity_check"),
        )
        final_fk = l3.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-50", "Final foreign-key integrity", not final_fk, len(final_fk))

        zero_sleep = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr
            JOIN normalized_interval_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT' AND x.duration_seconds=0
            """,
        )
        add(checks, "FINAL-51", "Zero-duration Sleep segments preserved", zero_sleep == 2, zero_sleep)
        bucket_resolved_incorrectly = scalar(
            l3,
            """
            SELECT COUNT(*) FROM fact_registry fr
            JOIN normalized_bucket_facts x ON x.fact_id=fr.id
            WHERE fr.status='CURRENT'
              AND (x.bucket_width_seconds IS NOT NULL OR x.bucket_semantics<>'VENDOR_UNRESOLVED')
            """,
        )
        add(
            checks,
            "FINAL-52",
            "Bucket width remains unresolved",
            bucket_resolved_incorrectly == 0,
            bucket_resolved_incorrectly,
        )
        calorie_units = {
            row[0]
            for row in l3.execute(
                """
                SELECT DISTINCT x.unit FROM fact_registry fr
                JOIN normalized_bucket_facts x ON x.fact_id=fr.id
                WHERE fr.status='CURRENT' AND fr.metric='calories'
                """
            )
        }
        add(
            checks,
            "FINAL-53",
            "Calories physical unit remains unresolved",
            calorie_units == {"vendor_calories"},
            sorted(calorie_units),
        )
        canonical_sleep = scalar(
            l3,
            """
            SELECT COUNT(*) FROM derived_features WHERE status='CURRENT'
              AND (lower(feature_name) LIKE '%canonical%' OR lower(scope_type) LIKE '%night%')
            """,
        ) + scalar(
            l3,
            "SELECT COUNT(*) FROM fact_registry WHERE status='CURRENT' AND lower(metric) LIKE '%canonical%'",
        )
        add(checks, "FINAL-54", "No canonical Sleep night invented", canonical_sleep == 0, canonical_sleep)
        pipeline_failures = scalar(
            l3, "SELECT COUNT(*) FROM pipeline_runs WHERE status NOT IN ('PASS')"
        )
        add(checks, "FINAL-55", "Production pipeline runs all PASS", pipeline_failures == 0, pipeline_failures)

        fact_counts = dict(
            l3.execute(
                """
                SELECT metric,COUNT(*) FROM fact_registry
                WHERE status='CURRENT' GROUP BY metric ORDER BY metric
                """
            ).fetchall()
        )
        feature_counts = dict(
            l3.execute(
                """
                SELECT feature_name,COUNT(*) FROM derived_features
                WHERE status='CURRENT' GROUP BY feature_name ORDER BY feature_name
                """
            ).fetchall()
        )
        summary = {
            "schema_version": schema_version,
            "current_fact_total": sum(fact_counts.values()),
            "current_facts_by_metric": fact_counts,
            "current_quality_assessments": scalar(
                l3, "SELECT COUNT(*) FROM quality_assessments WHERE status='CURRENT'"
            ),
            "current_resolution_decisions": scalar(
                l3, "SELECT COUNT(*) FROM source_resolution_decisions WHERE status='CURRENT'"
            ),
            "current_feature_total": sum(feature_counts.values()),
            "current_features_by_name": feature_counts,
            "definitions": len(registry),
            "migrations": len(migrations),
            "checkpoints": len(checkpoint_rows),
        }
    finally:
        l2.close()
        l3.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "layer": "Layer 3 = Feature Engineering",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l2_source": str(l2_path),
        "l3_database": str(l3_path),
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "summary": summary,
        "stage_results": {
            "L3A": l3a.get("status"),
            "L3B": l3b.get("status"),
            "L3C": l3c.get("status"),
            "FULL_REBUILD": rebuild.get("status"),
            "SEMANTIC_EQUIVALENCE": semantic.get("status"),
        },
        "known_limitations": [
            "Sleep stages and sleep/awake states are Xiaomi vendor inferences, not physiological ground truth.",
            "Canonical Sleep night grouping remains unresolved because the sample is insufficient for a stable gap threshold.",
            "Activity bucket widths remain vendor-unresolved.",
            "Calories retain vendor_calories because the physical unit and component meaning are unresolved.",
            "Some multi-source conflicts remain explicitly UNRESOLVED.",
            "Resting-heart-rate coverage is sparse (2 daily facts).",
            "No external physiological ground truth is available.",
        ],
        "checks": checks,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status","checks_passed","checks_failed","checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
