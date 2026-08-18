"""Layer 4 final audit.

Cross-checks the L4 acceptance, full-rebuild, and semantic-equivalence evidence
against direct database invariants, confirms the sealed L3 upstream was not
modified, and confirms no Layer 5/6 output leaked into Layer 4.
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
    checks.append(
        {"id": check_id, "name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence}
    )


def artifact_check(report, check_id):
    return next((item for item in report.get("checks", []) if item.get("id") == check_id), {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l3_path = Path(args.l3).resolve()
    l4_path = Path(args.l4).resolve()
    acceptance = load_report(root / "L4_ACCEPTANCE.json")
    rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
    semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")

    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(l4_path), uri=True)
    l4.row_factory = sqlite3.Row
    checks = []
    try:
        add(checks, "FINAL-01", "SQLite integrity", scalar(l4, "PRAGMA integrity_check") == "ok", scalar(l4, "PRAGMA integrity_check"))
        fk = l4.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-02", "Foreign keys", not fk, len(fk))
        add(checks, "FINAL-03", "WAL journal mode", scalar(l4, "PRAGMA journal_mode") == "wal", scalar(l4, "PRAGMA journal_mode"))
        add(
            checks,
            "FINAL-04",
            "L3/L4 isolation",
            l3_path != l4_path and l3_path.parent != l4_path.parent,
            {"l3": str(l3_path), "l4": str(l4_path), "l3_mode": "ro"},
        )
        schema_version = scalar(l4, "PRAGMA user_version")
        add(checks, "FINAL-05", "Production schema version", schema_version == 2, schema_version)

        migrations = l4.execute("SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version").fetchall()
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
        registry = l4.execute("SELECT definition_id,definition_version,status,definition_sha256 FROM definition_registry").fetchall()
        registry_ok = len(registry) == 4 and all(
            definition_files.get((row["definition_id"], row["definition_version"])) == row["definition_sha256"]
            and row["status"] == "ACTIVE"
            for row in registry
        )
        add(checks, "FINAL-08", "Definition registry integrity", registry_ok, len(registry))

        windows = {row["window_days"] for row in l4.execute("SELECT DISTINCT window_days FROM rolling_baselines WHERE status='CURRENT'")}
        add(checks, "FINAL-09", "Windows 7/28/90", windows == {7, 28, 90}, sorted(windows))

        leak = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines rb
            JOIN baseline_feature_inputs bfi ON bfi.baseline_id=rb.id
            WHERE rb.status='CURRENT' AND bfi.l3_local_date >= rb.as_of_date
            """,
        )
        add(checks, "FINAL-10", "No look-ahead leakage", leak == 0, leak)

        published = l4.execute(
            "SELECT * FROM rolling_baselines WHERE status='CURRENT' AND maturity IN ('PROVISIONAL','ESTABLISHED')"
        ).fetchall()
        stats_ok = all(
            row["q10"] <= row["q25"] <= row["q50"] <= row["q75"] <= row["q90"]
            and abs((row["median"] or 0) - (row["q50"] or 0)) < 1e-9
            and row["mad"] is not None and row["mad"] >= 0 and row["mean"] is not None
            for row in published
        )
        add(checks, "FINAL-11", "Robust statistic invariants", stats_ok and len(published) > 0, len(published))

        insufficient_stats = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines
            WHERE status='CURRENT' AND maturity='INSUFFICIENT_HISTORY'
              AND (mean IS NOT NULL OR median IS NOT NULL OR q50 IS NOT NULL)
            """,
        )
        add(checks, "FINAL-12", "INSUFFICIENT_HISTORY publishes no statistics", insufficient_stats == 0, insufficient_stats)

        maturity_values = {row[0] for row in l4.execute("SELECT DISTINCT maturity FROM rolling_baselines")}
        add(
            checks,
            "FINAL-13",
            "Maturity values valid",
            maturity_values <= {"INSUFFICIENT_HISTORY", "PROVISIONAL", "ESTABLISHED"},
            sorted(maturity_values),
        )

        mixed_source = scalar(
            l4,
            "SELECT COUNT(*) FROM baseline_series WHERE status='CURRENT' AND source_class NOT IN ('NUMERIC_SOURCE','XIAOMI_GENERATED')",
        )
        add(checks, "FINAL-14", "Source isolation", mixed_source == 0, mixed_source)

        provenance_gaps = scalar(
            l4,
            """
            SELECT COUNT(*) FROM (
                SELECT rb.id FROM rolling_baselines rb
                LEFT JOIN baseline_feature_inputs bfi ON bfi.baseline_id=rb.id
                WHERE rb.status='CURRENT' AND rb.observation_count > 0
                GROUP BY rb.id HAVING COUNT(bfi.l3_feature_id)=0
            )
            """,
        )
        add(checks, "FINAL-15", "Baseline provenance complete", provenance_gaps == 0, provenance_gaps)

        canonical_sleep = scalar(
            l4,
            "SELECT COUNT(*) FROM baseline_series WHERE status='CURRENT' AND lower(feature_name) LIKE '%canonical%'",
        ) + scalar(
            l4,
            "SELECT COUNT(*) FROM rolling_baselines WHERE status='CURRENT' AND lower(attributes_json) LIKE '%canonical_night\":true%'",
        )
        add(checks, "FINAL-16", "No canonical Sleep night", canonical_sleep == 0, canonical_sleep)

        steps_calories_ok = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines rb
            JOIN baseline_series bs ON bs.id=rb.series_id
            WHERE rb.status='CURRENT' AND bs.feature_name IN ('steps.daily.sum','calories.daily.sum')
              AND rb.attributes_json NOT LIKE '%VENDOR_BUCKET_WIDTH_UNRESOLVED%'
            """,
        ) == 0
        add(checks, "FINAL-17", "Steps/calories coverage preserved", steps_calories_ok, None)

        resting = l4.execute(
            """
            SELECT DISTINCT maturity FROM rolling_baselines rb
            JOIN baseline_series bs ON bs.id=rb.series_id
            WHERE bs.feature_name='resting_heart_rate.daily.value'
              AND rb.status='CURRENT' AND rb.as_of_date='2026-08-17'
            """
        ).fetchall()
        add(checks, "FINAL-18", "Sparse series stay INSUFFICIENT_HISTORY", {row["maturity"] for row in resting} == {"INSUFFICIENT_HISTORY"}, [row["maturity"] for row in resting])

        frontier = scalar(l3, "SELECT COALESCE(MAX(id),0) FROM derived_features")
        checkpoint = l4.execute("SELECT last_l3_feature_id FROM processing_checkpoints WHERE pipeline_name='l4.baseline'").fetchone()
        add(
            checks,
            "FINAL-19",
            "Checkpoint correctness",
            bool(checkpoint and checkpoint["last_l3_feature_id"] == frontier),
            {"frontier": frontier, "checkpoint": checkpoint["last_l3_feature_id"] if checkpoint else None},
        )

        pipeline_failures = scalar(l4, "SELECT COUNT(*) FROM pipeline_runs WHERE status <> 'PASS'")
        add(checks, "FINAL-20", "Pipeline runs all PASS", pipeline_failures == 0, pipeline_failures)

        add(checks, "FINAL-21", "L4 acceptance PASS", acceptance.get("status") == "PASS" and acceptance.get("checks_failed") == 0, acceptance.get("status"))
        add(checks, "FINAL-22", "Full rebuild PASS", rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0, rebuild.get("status"))
        add(checks, "FINAL-23", "Semantic equivalence PASS", semantic.get("status") == "PASS" and semantic.get("checks_failed") == 0, semantic.get("status"))

        rebuild_checks = {item.get("name"): item.get("status") for item in rebuild.get("checks", [])}
        add(
            checks,
            "FINAL-24",
            "Production L3 unchanged during rebuild",
            rebuild_checks.get("production_l3_unchanged") == "PASS",
            rebuild_checks.get("production_l3_unchanged"),
        )
        add(
            checks,
            "FINAL-25",
            "Production L4 unchanged during rebuild",
            rebuild_checks.get("production_l4_unchanged") == "PASS",
            rebuild_checks.get("production_l4_unchanged"),
        )

        forbidden = (
            "anomaly", "trend", "risk", "readiness", "recovery", "sleep_score",
            "health_score", "wellness", "recommendation", "ai_reason",
            "diagnosis", "change_point",
        )
        series_names = [row[0].lower() for row in l4.execute("SELECT feature_name FROM baseline_series")]
        leakage = [token for token in forbidden if any(token in name for name in series_names)]
        add(checks, "FINAL-26", "No L5/L6 leakage", not leakage, leakage)

        add(
            checks,
            "FINAL-27",
            "Regression suite passes",
            artifact_check(acceptance, "L4-25").get("status") == "PASS",
            artifact_check(acceptance, "L4-25").get("evidence"),
        )

        add(checks, "FINAL-28", "Final SQLite integrity", scalar(l4, "PRAGMA integrity_check") == "ok", scalar(l4, "PRAGMA integrity_check"))
        final_fk = l4.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "FINAL-29", "Final foreign-key integrity", not final_fk, len(final_fk))

        summary = {
            "schema_version": schema_version,
            "current_series": scalar(l4, "SELECT COUNT(*) FROM baseline_series WHERE status='CURRENT'"),
            "current_baselines": scalar(l4, "SELECT COUNT(*) FROM rolling_baselines WHERE status='CURRENT'"),
            "maturity_distribution": dict(
                l4.execute(
                    "SELECT maturity,COUNT(*) n FROM rolling_baselines WHERE status='CURRENT' GROUP BY maturity"
                ).fetchall()
            ),
            "definitions": len(registry),
            "migrations": len(migrations),
        }
    finally:
        l3.close()
        l4.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "layer": "Layer 4 = Personal Baseline",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l3_source": str(l3_path),
        "l4_database": str(l4_path),
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
            "Canonical Sleep night grouping remains unresolved.",
            "Activity bucket widths remain vendor-unresolved.",
            "Calories retain vendor_calories; physical unit/component meaning unresolved.",
            "Resting-heart-rate coverage is sparse (2 daily facts); many baselines are INSUFFICIENT_HISTORY or PROVISIONAL by design.",
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
