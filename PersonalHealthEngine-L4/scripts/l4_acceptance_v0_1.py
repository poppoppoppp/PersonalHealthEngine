"""Layer 4 core acceptance.

Validates the production L4 baseline database against the sealed L3 upstream:
schema integrity, no-look-ahead semantics, robust statistics, maturity behavior,
source isolation, missing!=zero, provenance, sleep/steps/calories restrictions,
full-rebuild and semantic-equivalence evidence, and the regression suite.
"""

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def readonly_uri(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def scalar(db, sql, parameters=()):
    return db.execute(sql, parameters).fetchone()[0]


def add(checks, check_id, name, passed, evidence):
    checks.append(
        {"id": check_id, "name": name, "status": "PASS" if passed else "FAIL", "evidence": evidence}
    )


def load_report(path):
    if not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def recompute_quantile(sorted_values, q):
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    pos = q * (n - 1)
    lo = int(pos)
    hi = lo + 1 if lo < n - 1 else lo
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--l3", required=True)
    parser.add_argument("--l4", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    l3_path = Path(args.l3).resolve()
    l4_path = Path(args.l4).resolve()

    l3 = sqlite3.connect(readonly_uri(l3_path), uri=True)
    l3.row_factory = sqlite3.Row
    l4 = sqlite3.connect(readonly_uri(l4_path), uri=True)
    l4.row_factory = sqlite3.Row

    checks = []
    try:
        integrity = scalar(l4, "PRAGMA integrity_check")
        add(checks, "L4-01", "SQLite integrity", integrity == "ok", integrity)
        fk_errors = l4.execute("PRAGMA foreign_key_check").fetchall()
        add(checks, "L4-02", "Foreign keys", not fk_errors, len(fk_errors))
        add(
            checks,
            "L4-03",
            "L4/L3 database isolation",
            l3_path != l4_path and l3_path.parent != l4_path.parent,
            {"l3": str(l3_path), "l4": str(l4_path), "l3_mode": "ro"},
        )
        schema_version = scalar(l4, "PRAGMA user_version")
        add(checks, "L4-04", "Production schema version", schema_version == 2, schema_version)

        migrations = l4.execute(
            "SELECT version,name,checksum_sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
        migration_versions = [row["version"] for row in migrations]
        add(checks, "L4-05", "Migration chain", migration_versions == [1, 2], migration_versions)
        migration_matches = []
        for row in migrations:
            paths = list((root / "migrations").glob(f"{row['version']:03d}_*.sql"))
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest() if len(paths) == 1 else None
            migration_matches.append(digest == row["checksum_sha256"])
        add(
            checks,
            "L4-06",
            "Migration checksums",
            all(migration_matches),
            {"matched": sum(migration_matches), "total": len(migration_matches)},
        )

        definition_files = {}
        for path in (root / "definitions").rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            definition_files[(payload["definition_id"], payload["definition_version"])] = (
                path,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                payload["definition_type"],
            )
        registry = l4.execute(
            "SELECT definition_id,definition_version,definition_type,status,definition_sha256 "
            "FROM definition_registry"
        ).fetchall()
        registry_matches = []
        for row in registry:
            item = definition_files.get((row["definition_id"], row["definition_version"]))
            registry_matches.append(
                bool(item and item[1] == row["definition_sha256"] and item[2] == row["definition_type"] and row["status"] == "ACTIVE")
            )
        add(
            checks,
            "L4-07",
            "Definition registry integrity",
            len(registry) == 4 and all(registry_matches),
            {"registered": len(registry), "files": len(definition_files), "matched": sum(registry_matches)},
        )

        windows = {row["window_days"] for row in l4.execute(
            "SELECT DISTINCT window_days FROM rolling_baselines WHERE status='CURRENT'"
        )}
        add(checks, "L4-08", "Windows 7/28/90 present", windows == {7, 28, 90}, sorted(windows))

        leak = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines rb
            JOIN baseline_feature_inputs bfi ON bfi.baseline_id=rb.id
            WHERE rb.status='CURRENT' AND bfi.l3_local_date >= rb.as_of_date
            """,
        )
        add(checks, "L4-09", "No look-ahead leakage", leak == 0, leak)

        window_violations = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines rb
            JOIN baseline_feature_inputs bfi ON bfi.baseline_id=rb.id
            WHERE rb.status='CURRENT'
              AND bfi.l3_local_date < date(rb.as_of_date, '-' || rb.window_days || ' days')
            """,
        )
        add(checks, "L4-10", "Window lower boundary", window_violations == 0, window_violations)

        published = l4.execute(
            """
            SELECT * FROM rolling_baselines WHERE status='CURRENT'
              AND maturity IN ('PROVISIONAL','ESTABLISHED')
            """
        ).fetchall()
        ordering_ok = all(
            row["q10"] <= row["q25"] <= row["q50"] <= row["q75"] <= row["q90"]
            and abs((row["median"] or 0) - (row["q50"] or 0)) < 1e-9
            and (row["mad"] is not None and row["mad"] >= 0)
            and row["mean"] is not None
            for row in published
        )
        add(checks, "L4-11", "Robust statistic invariants", ordering_ok and len(published) > 0, len(published))

        insufficient_with_stats = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines
            WHERE status='CURRENT' AND maturity='INSUFFICIENT_HISTORY'
              AND (mean IS NOT NULL OR median IS NOT NULL OR mad IS NOT NULL
                   OR q10 IS NOT NULL OR q25 IS NOT NULL OR q50 IS NOT NULL
                   OR q75 IS NOT NULL OR q90 IS NOT NULL)
            """,
        )
        add(
            checks,
            "L4-12",
            "INSUFFICIENT_HISTORY publishes no statistics",
            insufficient_with_stats == 0,
            insufficient_with_stats,
        )

        resting = l4.execute(
            """
            SELECT DISTINCT maturity FROM rolling_baselines rb
            JOIN baseline_series bs ON bs.id=rb.series_id
            WHERE bs.feature_name='resting_heart_rate.daily.value'
              AND rb.status='CURRENT' AND rb.as_of_date='2026-08-17'
            """
        ).fetchall()
        add(
            checks,
            "L4-13",
            "Sparse series stay INSUFFICIENT_HISTORY",
            {row["maturity"] for row in resting} == {"INSUFFICIENT_HISTORY"},
            [row["maturity"] for row in resting],
        )

        mixed_source_series = scalar(
            l4,
            """
            SELECT COUNT(*) FROM baseline_series
            WHERE status='CURRENT' AND source_class NOT IN ('NUMERIC_SOURCE','XIAOMI_GENERATED')
            """,
        )
        add(checks, "L4-14", "Source isolation", mixed_source_series == 0, mixed_source_series)

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
        add(checks, "L4-15", "Baseline provenance complete", provenance_gaps == 0, provenance_gaps)

        input_count_mismatch = scalar(
            l4,
            """
            SELECT COUNT(*) FROM (
                SELECT rb.id, COUNT(bfi.l3_feature_id) n
                FROM rolling_baselines rb
                JOIN baseline_feature_inputs bfi ON bfi.baseline_id=rb.id
                WHERE rb.status='CURRENT'
                GROUP BY rb.id HAVING n <> rb.observation_count
            )
            """,
        )
        add(checks, "L4-16", "observation_count equals provenance inputs", input_count_mismatch == 0, input_count_mismatch)

        canonical_sleep = scalar(
            l4,
            """
            SELECT COUNT(*) FROM baseline_series
            WHERE status='CURRENT' AND lower(feature_name) LIKE '%canonical%'
            """,
        ) + scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines
            WHERE status='CURRENT' AND lower(attributes_json) LIKE '%canonical_night\":true%'
            """,
        )
        add(checks, "L4-17", "No canonical Sleep night invented", canonical_sleep == 0, canonical_sleep)

        steps_coverage_ok = scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines rb
            JOIN baseline_series bs ON bs.id=rb.series_id
            WHERE rb.status='CURRENT'
              AND bs.feature_name IN ('steps.daily.sum','calories.daily.sum')
              AND rb.attributes_json NOT LIKE '%VENDOR_BUCKET_WIDTH_UNRESOLVED%'
            """,
        ) == 0 and scalar(
            l4,
            """
            SELECT COUNT(*) FROM rolling_baselines rb
            JOIN baseline_series bs ON bs.id=rb.series_id
            WHERE rb.status='CURRENT'
              AND bs.feature_name IN ('steps.daily.sum','calories.daily.sum')
              AND rb.attributes_json LIKE '%VENDOR_BUCKET_WIDTH_UNRESOLVED%'
            """,
        ) > 0
        add(checks, "L4-18", "Steps/calories coverage preserved", steps_coverage_ok, None)

        # Independent recomputation of one real series.
        recompute_ok = False
        target = l4.execute(
            """
            SELECT rb.* FROM rolling_baselines rb
            JOIN baseline_series bs ON bs.id=rb.series_id
            WHERE rb.status='CURRENT' AND bs.feature_name='heart_rate.daily.mean'
              AND bs.source_class='NUMERIC_SOURCE' AND rb.window_days=7
              AND rb.as_of_date='2026-08-17'
            """
        ).fetchone()
        if target:
            feature_ids = [
                row["l3_feature_id"]
                for row in l4.execute(
                    "SELECT l3_feature_id FROM baseline_feature_inputs WHERE baseline_id=?",
                    (target["id"],),
                )
            ]
            values = sorted(
                row["value_num"]
                for row in l3.execute(
                    "SELECT value_num FROM derived_features WHERE id IN ("
                    + ",".join("?" for _ in feature_ids)
                    + ")",
                    feature_ids,
                )
            )
            median = recompute_quantile(values, 0.5)
            q25 = recompute_quantile(values, 0.25)
            q75 = recompute_quantile(values, 0.75)
            mad = recompute_quantile(sorted(abs(v - median) for v in values), 0.5)
            recompute_ok = (
                abs(median - target["median"]) < 1e-9
                and abs(q25 - target["q25"]) < 1e-9
                and abs(q75 - target["q75"]) < 1e-9
                and abs(mad - target["mad"]) < 1e-9
            )
        add(
            checks,
            "L4-19",
            "Independent robust statistic recomputation",
            recompute_ok,
            {"median": target["median"] if target else None},
        )

        forbidden = (
            "anomaly", "trend", "risk", "readiness", "recovery", "sleep_score",
            "health_score", "wellness", "recommendation", "ai_reason",
            "diagnosis", "change_point",
        )
        series_names = [row[0].lower() for row in l4.execute("SELECT feature_name FROM baseline_series")]
        maturity_values = {row[0] for row in l4.execute("SELECT DISTINCT maturity FROM rolling_baselines")}
        leakage = [token for token in forbidden if any(token in name for name in series_names)]
        maturity_leak = maturity_values - {"INSUFFICIENT_HISTORY", "PROVISIONAL", "ESTABLISHED"}
        add(
            checks,
            "L4-20",
            "No L5/L6 anomaly/score/reasoning leakage",
            not leakage and not maturity_leak,
            {"leaked_tokens": leakage, "unexpected_maturity": sorted(maturity_leak)},
        )

        frontier = scalar(l3, "SELECT COALESCE(MAX(id),0) FROM derived_features")
        checkpoint = l4.execute(
            "SELECT last_l3_feature_id FROM processing_checkpoints WHERE pipeline_name='l4.baseline'"
        ).fetchone()
        add(
            checks,
            "L4-21",
            "Checkpoint correctness",
            bool(checkpoint and checkpoint["last_l3_feature_id"] == frontier),
            {"frontier": frontier, "checkpoint": checkpoint["last_l3_feature_id"] if checkpoint else None},
        )

        pipeline_failures = scalar(
            l4, "SELECT COUNT(*) FROM pipeline_runs WHERE status <> 'PASS'"
        )
        add(checks, "L4-22", "Pipeline runs all PASS", pipeline_failures == 0, pipeline_failures)

        rebuild = load_report(root / "full_rebuild_acceptance" / "FULL_REBUILD_ACCEPTANCE.json")
        semantic = load_report(root / "full_rebuild_acceptance" / "SEMANTIC_EQUIVALENCE.json")
        add(
            checks,
            "L4-23",
            "Full rebuild PASS",
            rebuild.get("status") == "PASS" and rebuild.get("checks_failed") == 0,
            {"status": rebuild.get("status")},
        )
        add(
            checks,
            "L4-24",
            "Incremental/full semantic equivalence",
            semantic.get("status") == "PASS" and semantic.get("checks_failed") == 0,
            {"status": semantic.get("status")},
        )

        regression = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_l4_baseline_v0_1", "-q"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        add(
            checks,
            "L4-25",
            "Regression suite passes",
            regression.returncode == 0,
            {"returncode": regression.returncode, "tail": regression.stderr.splitlines()[-4:]},
        )

        series_count = scalar(l4, "SELECT COUNT(*) FROM baseline_series WHERE status='CURRENT'")
        baseline_count = scalar(l4, "SELECT COUNT(*) FROM rolling_baselines WHERE status='CURRENT'")
        maturity_counts = dict(
            l4.execute(
                "SELECT maturity,COUNT(*) n FROM rolling_baselines WHERE status='CURRENT' GROUP BY maturity"
            ).fetchall()
        )
        summary = {
            "schema_version": schema_version,
            "current_series": series_count,
            "current_baselines": baseline_count,
            "maturity_distribution": maturity_counts,
        }
    finally:
        l3.close()
        l4.close()

    passed = sum(check["status"] == "PASS" for check in checks)
    report = {
        "layer": "Layer 4 = Personal Baseline",
        "stage": "L4",
        "status": "PASS" if passed == len(checks) else "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "l3_source": str(l3_path),
        "l4_database": str(l4_path),
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
