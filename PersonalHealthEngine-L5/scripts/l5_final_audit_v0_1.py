"""Layer 5 final audit.

Cross-checks the L5 acceptance, full-rebuild, and semantic-equivalence evidence against
direct database invariants, confirms the sealed L3/L4 upstreams were not modified, and
confirms no Layer 6 output leaked into Layer 5.
"""

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def load_report(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def add(checks, check_id, name, passed, evidence):
    checks.append({"id": check_id, "name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def artifact_check(report, check_id):
    return next((item for item in report.get("checks", []) if item.get("id") == check_id), {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l3_path = Path(args.l3).resolve()
    l4_path = Path(args.l4).resolve()
    l5_path = Path(args.l5).resolve()
    acceptance = load_report(root / "L5_ACCEPTANCE.json")
    rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
    semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")

    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(l4_path), uri=True)
    l4.row_factory = sqlite3.Row
    l5 = sqlite3.connect(readonly_uri(l5_path), uri=True)
    l5.row_factory = sqlite3.Row
    checks = []
    try:
        add(checks, "FINAL-01", "SQLite integrity", scalar(l5, "PRAGMA integrity_check") == "ok", scalar(l5, "PRAGMA integrity_check"))
        fk = l5.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-02", "Foreign keys", not fk, len(fk))
        add(checks, "FINAL-03", "WAL journal mode", scalar(l5, "PRAGMA journal_mode") == "wal", scalar(l5, "PRAGMA journal_mode"))
        add(checks, "FINAL-04", "L5/L3/L4 isolation", l5_path != l3_path and l5_path != l4_path and l5_path.parent not in (l3_path.parent, l4_path.parent), str(l5_path))
        schema_version = scalar(l5, "PRAGMA user_version")
        add(checks, "FINAL-05", "Production schema version", schema_version == 2, schema_version)

        migrations = l5.execute("SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version").fetchall()
        add(checks, "FINAL-06", "Migration chain", [row["version"] for row in migrations] == [1, 2], [row["version"] for row in migrations])
        migration_matches = []
        for row in migrations:
            paths = list((root / "migrations").glob(f"{row['version']:03d}_*.sql"))
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths) == 1 else None
            migration_matches.append(digest == row["checksum_sha256"])
        add(checks, "FINAL-07", "Migration checksums", all(migration_matches), sum(migration_matches))

        definition_files = {}
        for path in (root / "definitions").rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = hashlib.sha256(path.read_bytes()).hexdigest()
        registry = l5.execute("SELECT definition_id,definition_version,status,definition_sha256 FROM definition_registry").fetchall()
        registry_ok = len(registry) == 6 and all(
            definition_files.get((row["definition_id"], row["definition_version"])) == row["definition_sha256"] and row["status"] == "ACTIVE"
            for row in registry
        )
        add(checks, "FINAL-08", "Definition registry integrity", registry_ok, len(registry))

        leak = scalar(
            l5,
            """
            SELECT COUNT(*) FROM deviation_analytics d
            JOIN analytics_baseline_inputs b ON b.analytic_type='DEVIATION' AND b.analytic_id=d.id
            WHERE d.status='CURRENT' AND b.l4_baseline_as_of_date <> d.feature_date
            """,
        )
        add(checks, "FINAL-09", "No look-ahead leakage", leak == 0, leak)

        bad_dev = scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT' AND deviation_class='INSUFFICIENT_BASELINE' AND baseline_maturity <> 'INSUFFICIENT_HISTORY'")
        add(checks, "FINAL-10", "Deviation + baseline-maturity correctness", bad_dev == 0, bad_dev)

        insufficient_baseline = scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT' AND deviation_class='INSUFFICIENT_BASELINE'")
        add(checks, "FINAL-11", "Baseline maturity participates", insufficient_baseline > 0, insufficient_baseline)

        change_classes = {row[0] for row in l5.execute("SELECT DISTINCT change_class FROM change_point_analytics WHERE status='CURRENT'")}
        add(checks, "FINAL-12", "Change detection conservative", change_classes == {"INSUFFICIENT_EVIDENCE"}, sorted(change_classes))

        mixed_source = scalar(l5, "SELECT COUNT(*) FROM analytics_series WHERE status='CURRENT' AND source_class NOT IN ('NUMERIC_SOURCE','XIAOMI_GENERATED')")
        add(checks, "FINAL-13", "Source isolation", mixed_source == 0, mixed_source)

        provenance_gaps = scalar(
            l5,
            """
            SELECT COUNT(*) FROM (
                SELECT d.id FROM deviation_analytics d
                LEFT JOIN analytics_l3_inputs i ON i.analytic_type='DEVIATION' AND i.analytic_id=d.id
                WHERE d.status='CURRENT' GROUP BY d.id HAVING COUNT(i.l3_feature_id)=0
            )
            """,
        )
        add(checks, "FINAL-14", "Provenance complete", provenance_gaps == 0, provenance_gaps)

        sleep_p = scalar(l5, "SELECT COUNT(*) FROM persistence_analytics p JOIN analytics_series s ON s.id=p.series_id WHERE p.status='CURRENT' AND s.scope_type='SOURCE_EPISODE'")
        canonical = scalar(l5, "SELECT COUNT(*) FROM analytics_series WHERE status='CURRENT' AND lower(feature_name) LIKE '%canonical%'")
        add(checks, "FINAL-15", "Sleep: no canonical night, no persistence/trend", sleep_p == 0 and canonical == 0, {"sleep_p": sleep_p, "canonical": canonical})

        causal = scalar(l5, "SELECT COUNT(*) FROM relationship_analytics WHERE status='CURRENT' AND lower(attributes_json) LIKE '%causal_inference\":true%'")
        add(checks, "FINAL-16", "Relationship is association only", causal == 0, causal)

        frontier_l3 = scalar(l3, "SELECT COALESCE(MAX(id),0) FROM derived_features")
        frontier_l4 = scalar(l4, "SELECT COALESCE(MAX(id),0) FROM rolling_baselines")
        checkpoint = l5.execute("SELECT last_l3_feature_id,last_l4_baseline_id FROM processing_checkpoints WHERE pipeline_name='l5.analytics'").fetchone()
        add(checks, "FINAL-17", "Checkpoint correctness", bool(checkpoint and checkpoint["last_l3_feature_id"] == frontier_l3 and checkpoint["last_l4_baseline_id"] == frontier_l4), {"fl3": frontier_l3, "fl4": frontier_l4})

        pipeline_failures = scalar(l5, "SELECT COUNT(*) FROM pipeline_runs WHERE status <> 'PASS'")
        add(checks, "FINAL-18", "Pipeline runs all PASS", pipeline_failures == 0, pipeline_failures)

        add(checks, "FINAL-19", "L5 acceptance PASS", acceptance.get("status") == "PASS" and acceptance.get("checks_failed") == 0, acceptance.get("status"))
        add(checks, "FINAL-20", "Full rebuild PASS", rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0, rebuild.get("status"))
        add(checks, "FINAL-21", "Semantic equivalence PASS", semantic.get("status") == "PASS" and semantic.get("checks_failed") == 0, semantic.get("status"))

        rebuild_checks = {item.get("name"): item.get("status") for item in rebuild.get("checks", [])}
        add(checks, "FINAL-22", "Production L3 unchanged", rebuild_checks.get("production_l3_unchanged") == "PASS", rebuild_checks.get("production_l3_unchanged"))
        add(checks, "FINAL-23", "Production L4 unchanged", rebuild_checks.get("production_l4_unchanged") == "PASS", rebuild_checks.get("production_l4_unchanged"))
        add(checks, "FINAL-24", "Production L5 unchanged", rebuild_checks.get("production_l5_unchanged") == "PASS", rebuild_checks.get("production_l5_unchanged"))

        forbidden = ("anomaly", "diagnos", "risk", "causal", "recommend", "readiness", "recovery", "sleep_score", "health_score", "wellness")
        names = [row[0].lower() for row in l5.execute("SELECT feature_name FROM analytics_series")]
        leakage = [t for t in forbidden if any(t in n for n in names)]
        add(checks, "FINAL-25", "No L6 leakage", not leakage, leakage)

        add(checks, "FINAL-26", "Regression suite passes", artifact_check(acceptance, "L5-23").get("status") == "PASS", artifact_check(acceptance, "L5-23").get("evidence"))

        add(checks, "FINAL-27", "Final SQLite integrity", scalar(l5, "PRAGMA integrity_check") == "ok", scalar(l5, "PRAGMA integrity_check"))
        final_fk = l5.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-28", "Final foreign-key integrity", not final_fk, len(final_fk))

        summary = {
            "schema_version": schema_version,
            "current_series": scalar(l5, "SELECT COUNT(*) FROM analytics_series WHERE status='CURRENT'"),
            "current_deviation": scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT'"),
            "current_persistence": scalar(l5, "SELECT COUNT(*) FROM persistence_analytics WHERE status='CURRENT'"),
            "current_trend": scalar(l5, "SELECT COUNT(*) FROM trend_analytics WHERE status='CURRENT'"),
            "current_change": scalar(l5, "SELECT COUNT(*) FROM change_point_analytics WHERE status='CURRENT'"),
            "current_relationship": scalar(l5, "SELECT COUNT(*) FROM relationship_analytics WHERE status='CURRENT'"),
            "definitions": len(registry),
            "migrations": len(migrations),
        }
    finally:
        l3.close()
        l4.close()
        l5.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "layer": "Layer 5 = Health Analytics",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l3_source": str(l3_path),
        "l4_source": str(l4_path),
        "l5_database": str(l5_path),
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "summary": summary,
        "stage_results": {
            "ACCEPTANCE": acceptance.get("status"),
            "FULL_REBUILD": rebuild.get("status"),
            "SEMANTIC_EQUIVALENCE": semantic.get("status"),
        },
        "known_limitations": [
            "Sleep stages/awake states are Xiaomi vendor inferences, not physiological truth.",
            "Canonical Sleep night grouping remains unresolved; Sleep series have deviation only.",
            "Activity bucket widths and calories physical semantics remain unresolved.",
            "Resting-heart-rate coverage is sparse; its timezone offset is NULL, so it does not share a source context with other metrics for cross-metric relationships.",
            "Overall history is short, so most baselines are INSUFFICIENT_HISTORY/PROVISIONAL and change detection is INSUFFICIENT_EVIDENCE.",
        ],
        "checks": checks,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
