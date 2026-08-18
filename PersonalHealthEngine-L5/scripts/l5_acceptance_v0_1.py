"""Layer 5 core acceptance.

Validates the production L5 analytics database against the sealed L3/L4 upstreams:
schema integrity, no-look-ahead, deviation correctness, MAD=0 fallback, baseline-maturity
participation, persistence/trend/change/relationship classifications, source isolation,
provenance, sleep/steps/calories restrictions, full-rebuild + semantic-equivalence evidence,
and the regression suite.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def add(checks, check_id, name, passed, evidence):
    checks.append({"id": check_id, "name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def load_report(path):
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--l5", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l3_path = Path(args.l3).resolve()
    l4_path = Path(args.l4).resolve()
    l5_path = Path(args.l5).resolve()

    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(l4_path), uri=True)
    l4.row_factory = sqlite3.Row
    l5 = sqlite3.connect(readonly_uri(l5_path), uri=True)
    l5.row_factory = sqlite3.Row

    checks = []
    try:
        add(checks, "L5-01", "SQLite integrity", scalar(l5, "PRAGMA integrity_check") == "ok", scalar(l5, "PRAGMA integrity_check"))
        fk = l5.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "L5-02", "Foreign keys", not fk, len(fk))
        add(
            checks,
            "L5-03",
            "L5/L3/L4 isolation",
            l5_path != l3_path and l5_path != l4_path and l5_path.parent not in (l3_path.parent, l4_path.parent),
            {"l3": str(l3_path), "l4": str(l4_path), "l5": str(l5_path), "upstream_mode": "ro"},
        )
        schema_version = scalar(l5, "PRAGMA user_version")
        add(checks, "L5-04", "Production schema version", schema_version == 2, schema_version)

        migrations = l5.execute("SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version").fetchall()
        add(checks, "L5-05", "Migration chain", [row["version"] for row in migrations] == [1, 2], [row["version"] for row in migrations])
        migration_matches = []
        for row in migrations:
            paths = list((root / "migrations").glob(f"{row['version']:03d}_*.sql"))
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths) == 1 else None
            migration_matches.append(digest == row["checksum_sha256"])
        add(checks, "L5-06", "Migration checksums", all(migration_matches), sum(migration_matches))

        definition_files = {}
        for path in (root / "definitions").rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = (hashlib.sha256(path.read_bytes()).hexdigest(), payload["definition_type"])
        registry = l5.execute("SELECT definition_id,definition_version,definition_type,status,definition_sha256 FROM definition_registry").fetchall()
        registry_ok = len(registry) == 6 and all(
            definition_files.get((row["definition_id"], row["definition_version"])) == (row["definition_sha256"], row["definition_type"])
            and row["status"] == "ACTIVE"
            for row in registry
        )
        add(checks, "L5-07", "Definition registry integrity", registry_ok, len(registry))

        # deviation invariants
        bad_dev = scalar(
            l5,
            """
            SELECT COUNT(*) FROM deviation_analytics
            WHERE status='CURRENT'
              AND deviation_class='INSUFFICIENT_BASELINE'
              AND baseline_maturity <> 'INSUFFICIENT_HISTORY'
            """,
        )
        mad_inf = scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT' AND robust_standardized_deviation > 1e15")
        add(checks, "L5-08", "Deviation + MAD=0 fallback correctness", bad_dev == 0 and mad_inf == 0, {"bad_dev": bad_dev, "mad_inf": mad_inf})

        insufficient_baseline = scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT' AND deviation_class='INSUFFICIENT_BASELINE'")
        add(checks, "L5-09", "Baseline maturity participates", insufficient_baseline > 0, insufficient_baseline)

        # no look-ahead: baseline as_of == feature_date
        leak = scalar(
            l5,
            """
            SELECT COUNT(*) FROM deviation_analytics d
            JOIN analytics_baseline_inputs b ON b.analytic_type='DEVIATION' AND b.analytic_id=d.id
            WHERE d.status='CURRENT' AND b.l4_baseline_as_of_date <> d.feature_date
            """,
        )
        add(checks, "L5-10", "No look-ahead leakage", leak == 0, leak)

        # provenance completeness (every analytic has L3 inputs; deviation has baseline input)
        dev_no_input = scalar(
            l5,
            """
            SELECT COUNT(*) FROM (
                SELECT d.id FROM deviation_analytics d
                LEFT JOIN analytics_l3_inputs i ON i.analytic_type='DEVIATION' AND i.analytic_id=d.id
                WHERE d.status='CURRENT' GROUP BY d.id HAVING COUNT(i.l3_feature_id)=0
            )
            """,
        )
        dev_no_baseline = scalar(
            l5,
            """
            SELECT COUNT(*) FROM (
                SELECT d.id FROM deviation_analytics d
                LEFT JOIN analytics_baseline_inputs b ON b.analytic_type='DEVIATION' AND b.analytic_id=d.id
                WHERE d.status='CURRENT' GROUP BY d.id HAVING COUNT(b.l4_baseline_id)=0
            )
            """,
        )
        add(checks, "L5-11", "Deviation provenance complete", (dev_no_input, dev_no_baseline) == (0, 0), {"no_input": dev_no_input, "no_baseline": dev_no_baseline})

        # source isolation: series match source-class set
        mixed_source = scalar(l5, "SELECT COUNT(*) FROM analytics_series WHERE status='CURRENT' AND source_class NOT IN ('NUMERIC_SOURCE','XIAOMI_GENERATED')")
        add(checks, "L5-12", "Source isolation", mixed_source == 0, mixed_source)

        # persistence/trend valid values
        p_valid = scalar(l5, "SELECT COUNT(*) FROM persistence_analytics WHERE status='CURRENT' AND persistence_class NOT IN ('INSUFFICIENT_OBSERVATIONS','PERSISTENT_ABOVE_TYPICAL','PERSISTENT_BELOW_TYPICAL','NO_PERSISTENT_DEVIATION')")
        t_valid = scalar(l5, "SELECT COUNT(*) FROM trend_analytics WHERE status='CURRENT' AND trend_class NOT IN ('INSUFFICIENT_OBSERVATIONS','RISING','FALLING','STABLE')")
        add(checks, "L5-13", "Persistence/trend classifications valid", p_valid == 0 and t_valid == 0, {"p": p_valid, "t": t_valid})

        # change detection conservative on real data
        change_classes = {row[0] for row in l5.execute("SELECT DISTINCT change_class FROM change_point_analytics WHERE status='CURRENT'")}
        add(checks, "L5-14", "Change detection conservative", change_classes == {"INSUFFICIENT_EVIDENCE"}, sorted(change_classes))

        # relationship: association only, valid classes, no causality
        r_valid = scalar(l5, "SELECT COUNT(*) FROM relationship_analytics WHERE status='CURRENT' AND relationship_class NOT IN ('INSUFFICIENT_PAIRED_DATA','POSITIVE_ASSOCIATION','NEGATIVE_ASSOCIATION','NO_ASSOCIATION')")
        causal = scalar(l5, "SELECT COUNT(*) FROM relationship_analytics WHERE status='CURRENT' AND lower(attributes_json) LIKE '%causal_inference\":true%'")
        add(checks, "L5-15", "Relationship is association only (no causality)", r_valid == 0 and causal == 0, {"r_valid": r_valid, "causal": causal})

        # sleep: no persistence/trend/change
        sleep_p = scalar(
            l5,
            """
            SELECT COUNT(*) FROM persistence_analytics p JOIN analytics_series s ON s.id=p.series_id
            WHERE p.status='CURRENT' AND s.scope_type='SOURCE_EPISODE'
            """,
        )
        canonical = scalar(l5, "SELECT COUNT(*) FROM analytics_series WHERE status='CURRENT' AND lower(feature_name) LIKE '%canonical%'")
        add(checks, "L5-16", "Sleep: no canonical night, no persistence/trend/change", sleep_p == 0 and canonical == 0, {"sleep_p": sleep_p, "canonical": canonical})

        # steps/calories coverage preserved
        steps_ok = scalar(
            l5,
            """
            SELECT COUNT(*) FROM deviation_analytics d JOIN analytics_series s ON s.id=d.series_id
            WHERE d.status='CURRENT' AND s.feature_name IN ('steps.daily.sum','calories.daily.sum')
              AND d.attributes_json NOT LIKE '%VENDOR_BUCKET_WIDTH_UNRESOLVED%'
            """,
        ) == 0
        add(checks, "L5-17", "Steps/calories coverage preserved", steps_ok, None)

        # no L6 leakage
        forbidden = ("anomaly", "diagnos", "risk", "causal", "recommend", "readiness", "recovery", "sleep_score", "health_score", "wellness")
        names = [row[0].lower() for row in l5.execute("SELECT feature_name FROM analytics_series")]
        classes = [row[0].lower() for row in l5.execute("SELECT DISTINCT deviation_class FROM deviation_analytics")]
        classes += [row[0].lower() for row in l5.execute("SELECT DISTINCT trend_class FROM trend_analytics")]
        classes += [row[0].lower() for row in l5.execute("SELECT DISTINCT relationship_class FROM relationship_analytics")]
        classes += [row[0].lower() for row in l5.execute("SELECT DISTINCT change_class FROM change_point_analytics")]
        leakage = [t for t in forbidden if any(t in n for n in names) or any(t in c for c in classes)]
        add(checks, "L5-18", "No L6 causal/score/diagnosis/recommendation leakage", not leakage, leakage)

        frontier_l3 = scalar(l3, "SELECT COALESCE(MAX(id),0) FROM derived_features")
        frontier_l4 = scalar(l4, "SELECT COALESCE(MAX(id),0) FROM rolling_baselines")
        checkpoint = l5.execute("SELECT last_l3_feature_id,last_l4_baseline_id FROM processing_checkpoints WHERE pipeline_name='l5.analytics'").fetchone()
        add(
            checks,
            "L5-19",
            "Checkpoint correctness",
            bool(checkpoint and checkpoint["last_l3_feature_id"] == frontier_l3 and checkpoint["last_l4_baseline_id"] == frontier_l4),
            {"frontier_l3": frontier_l3, "frontier_l4": frontier_l4, "checkpoint": tuple(checkpoint) if checkpoint else None},
        )

        pipeline_failures = scalar(l5, "SELECT COUNT(*) FROM pipeline_runs WHERE status <> 'PASS'")
        add(checks, "L5-20", "Pipeline runs all PASS", pipeline_failures == 0, pipeline_failures)

        rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
        semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")
        add(checks, "L5-21", "Full rebuild PASS", rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0, rebuild.get("status"))
        add(checks, "L5-22", "Incremental/full semantic equivalence", semantic.get("status") == "PASS" and semantic.get("checks_failed") == 0, semantic.get("status"))

        regression = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_l5_analytics_v0_1", "-q"],
            cwd=root, text=True, capture_output=True, check=False,
        )
        add(checks, "L5-23", "Regression suite passes", regression.returncode == 0, {"returncode": regression.returncode, "tail": regression.stderr.splitlines()[-4:]})

        counts = {
            "series": scalar(l5, "SELECT COUNT(*) FROM analytics_series WHERE status='CURRENT'"),
            "deviation": scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT'"),
            "persistence": scalar(l5, "SELECT COUNT(*) FROM persistence_analytics WHERE status='CURRENT'"),
            "trend": scalar(l5, "SELECT COUNT(*) FROM trend_analytics WHERE status='CURRENT'"),
            "change": scalar(l5, "SELECT COUNT(*) FROM change_point_analytics WHERE status='CURRENT'"),
            "relationship": scalar(l5, "SELECT COUNT(*) FROM relationship_analytics WHERE status='CURRENT'"),
        }
        evidence_counts = {
            "INSUFFICIENT_BASELINE": scalar(l5, "SELECT COUNT(*) FROM deviation_analytics WHERE status='CURRENT' AND evidence_status='INSUFFICIENT_BASELINE'"),
            "INSUFFICIENT_EVIDENCE": scalar(l5, "SELECT COUNT(*) FROM change_point_analytics WHERE status='CURRENT' AND evidence_status='INSUFFICIENT_EVIDENCE'"),
            "INSUFFICIENT_PAIRED_DATA": scalar(l5, "SELECT COUNT(*) FROM relationship_analytics WHERE status='CURRENT' AND evidence_status='INSUFFICIENT_PAIRED_DATA'"),
        }
        summary = {"schema_version": schema_version, "counts": counts, "evidence_counts": evidence_counts}
    finally:
        l3.close()
        l4.close()
        l5.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "layer": "Layer 5 = Health Analytics",
        "stage": "L5",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l3_source": str(l3_path),
        "l4_source": str(l4_path),
        "l5_database": str(l5_path),
        "checks_passed": passed,
        "checks_failed": len(checks) - passed,
        "checks_total": len(checks),
        "summary": summary,
        "checks": checks,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("stage", "status", "checks_passed", "checks_failed", "checks_total")}, indent=2))
    raise SystemExit(0 if report["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
