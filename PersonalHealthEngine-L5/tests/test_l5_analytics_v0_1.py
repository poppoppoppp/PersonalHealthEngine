import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(r"D:\PersonalHealthEngine-L5")
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from l5_analytics_core_v0_1 import (  # noqa: E402
    classify_persistence,
    classify_relationship,
    classify_trend,
    detect_change,
    deviation_metrics,
    median,
    quantile,
    spearman_rho,
    theil_sen_slope,
)

L3_PRODUCTION = Path(r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3")
L4_PRODUCTION = Path(r"D:\PersonalHealthEngine-L4\db\personal_health_baselines.sqlite3")
APPLY_MIGRATIONS = SCRIPTS / "apply_migrations_v0_1.py"
MATERIALIZER = SCRIPTS / "l5_analytics_materializer_v0_1.py"

DEFS = {
    "deviation": ROOT / "definitions" / "deviation" / "l5a_deviation_robust_v0_1.json",
    "persistence": ROOT / "definitions" / "persistence" / "l5b_persistence_v0_1.json",
    "trend": ROOT / "definitions" / "trend" / "l5b_trend_robust_v0_1.json",
    "change": ROOT / "definitions" / "change" / "l5c_change_point_v0_1.json",
    "relationship": ROOT / "definitions" / "relationship" / "l5d_relationship_v0_1.json",
    "evidence": ROOT / "definitions" / "evidence" / "l5e_evidence_v0_1.json",
}

RELATIVE_UNITS = {"bpm", "percent", "seconds", "steps", "vendor_calories"}


def baseline(median, mad, q10, q25, q50, q75, q90, maturity="ESTABLISHED"):
    return {
        "maturity": maturity, "median": median, "mad": mad,
        "q10": q10, "q25": q25, "q50": q50, "q75": q75, "q90": q90,
    }


class CoreUnitTests(unittest.TestCase):
    def test_deviation_at_center(self):
        m = deviation_metrics(50.0, "bpm", baseline(50, 5, 40, 45, 50, 55, 60), RELATIVE_UNITS)
        self.assertEqual(m["deviation_class"], "WITHIN_TYPICAL_RANGE")
        self.assertEqual(m["absolute_deviation"], 0.0)
        self.assertEqual(m["deviation_side"], "WITHIN")

    def test_deviation_above_and_below(self):
        above = deviation_metrics(80.0, "bpm", baseline(50, 5, 40, 45, 50, 55, 60), RELATIVE_UNITS)
        self.assertEqual(above["deviation_class"], "ABOVE_TYPICAL_RANGE")
        self.assertEqual(above["deviation_side"], "ABOVE")
        self.assertEqual(above["robust_standardized_deviation"], 6.0)
        below = deviation_metrics(20.0, "bpm", baseline(50, 5, 40, 45, 50, 55, 60), RELATIVE_UNITS)
        self.assertEqual(below["deviation_class"], "BELOW_TYPICAL_RANGE")
        self.assertEqual(below["deviation_side"], "BELOW")

    def test_mad_zero_fallback(self):
        m = deviation_metrics(60.0, "bpm", baseline(50, 0, 50, 50, 50, 50, 50), RELATIVE_UNITS)
        self.assertIsNone(m["robust_standardized_deviation"])
        self.assertEqual(m["robust_z_unavailable_reason"], "MAD_ZERO")
        self.assertEqual(m["deviation_class"], "ABOVE_TYPICAL_RANGE")

    def test_insufficient_baseline(self):
        m = deviation_metrics(60.0, "bpm", baseline(None, None, None, None, None, None, None, "INSUFFICIENT_HISTORY"), RELATIVE_UNITS)
        self.assertEqual(m["deviation_class"], "INSUFFICIENT_BASELINE")
        self.assertEqual(m["evidence_status"], "INSUFFICIENT_BASELINE")
        self.assertIsNone(m["absolute_deviation"])

    def test_relative_deviation_unit_policy(self):
        with_rel = deviation_metrics(60.0, "bpm", baseline(50, 5, 40, 45, 50, 55, 60), RELATIVE_UNITS)
        self.assertEqual(with_rel["relative_deviation_applicable"], 1)
        self.assertAlmostEqual(with_rel["relative_deviation"], 0.2)
        without = deviation_metrics(6.0, "count", baseline(5, 1, 4, 4, 5, 6, 6), RELATIVE_UNITS)
        self.assertEqual(without["relative_deviation_applicable"], 0)
        self.assertIsNone(without["relative_deviation"])

    def test_trend_rising_falling_flat_insufficient(self):
        rising = classify_trend([(1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0)], 3, 0.5)
        self.assertEqual(rising["trend_class"], "RISING")
        falling = classify_trend([(1, 4.0), (2, 3.0), (3, 2.0), (4, 1.0)], 3, 0.5)
        self.assertEqual(falling["trend_class"], "FALLING")
        flat = classify_trend([(1, 5.0), (2, 5.0), (3, 5.0)], 3, 0.5)
        self.assertEqual(flat["trend_class"], "STABLE")
        few = classify_trend([(1, 1.0), (2, 2.0)], 3, 0.5)
        self.assertEqual(few["trend_class"], "INSUFFICIENT_OBSERVATIONS")

    def test_persistence(self):
        above = classify_persistence(["ABOVE_TYPICAL_RANGE", "ABOVE_TYPICAL_RANGE", "ABOVE_TYPICAL_RANGE"], ["ESTABLISHED"] * 3, 3)
        self.assertEqual(above["persistence_class"], "PERSISTENT_ABOVE_TYPICAL")
        below = classify_persistence(["BELOW_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE", "BELOW_TYPICAL_RANGE"], ["ESTABLISHED"] * 3, 3)
        self.assertEqual(below["persistence_class"], "PERSISTENT_BELOW_TYPICAL")
        none = classify_persistence(["WITHIN_TYPICAL_RANGE", "ABOVE_TYPICAL_RANGE"], ["ESTABLISHED", "ESTABLISHED"], 3)
        self.assertEqual(none["persistence_class"], "INSUFFICIENT_OBSERVATIONS")
        single = classify_persistence(["ABOVE_TYPICAL_RANGE", "WITHIN_TYPICAL_RANGE", "ABOVE_TYPICAL_RANGE"], ["ESTABLISHED"] * 3, 3)
        self.assertEqual(single["persistence_class"], "NO_PERSISTENT_DEVIATION")

    def test_change_detection(self):
        points = [(f"2026-08-{i:02d}", 1.0) for i in range(1, 6)] + [(f"2026-08-{i:02d}", 10.0) for i in range(6, 11)]
        c = detect_change(points, 10, 4, 3.0)
        self.assertEqual(c["change_class"], "CHANGE_DETECTED")
        flat = [(f"2026-08-{i:02d}", 5.0) for i in range(1, 11)]
        f = detect_change(flat, 10, 4, 3.0)
        self.assertEqual(f["change_class"], "NO_CHANGE")
        few = detect_change([("2026-08-01", 1.0)] * 5, 10, 4, 3.0)
        self.assertEqual(few["change_class"], "INSUFFICIENT_EVIDENCE")

    def test_relationship(self):
        pos = classify_relationship([1, 2, 3, 4, 5], [2, 4, 6, 8, 10], 5, 0.7)
        self.assertEqual(pos["relationship_class"], "POSITIVE_ASSOCIATION")
        neg = classify_relationship([1, 2, 3, 4, 5], [10, 8, 6, 4, 2], 5, 0.7)
        self.assertEqual(neg["relationship_class"], "NEGATIVE_ASSOCIATION")
        few = classify_relationship([1, 2], [3, 4], 5, 0.7)
        self.assertEqual(few["relationship_class"], "INSUFFICIENT_PAIRED_DATA")

    def test_theil_sen_and_spearman(self):
        self.assertEqual(theil_sen_slope([(0, 1.0), (1, 2.0), (2, 3.0)]), 1.0)
        self.assertEqual(spearman_rho([(0, 1.0), (1, 2.0), (2, 3.0)]), 1.0)
        self.assertEqual(spearman_rho([(0, 3.0), (1, 2.0), (2, 1.0)]), -1.0)


def _create_synthetic_l3(path, rows):
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            """
            CREATE TABLE derived_features (
                id INTEGER PRIMARY KEY, feature_name TEXT, scope_type TEXT, scope_key TEXT,
                local_date TEXT, value_num REAL, value_code TEXT, unit TEXT, provider TEXT,
                source_sid TEXT, source_class TEXT, timezone_name TEXT,
                timezone_offset_seconds INTEGER, coverage_status TEXT, attributes_json TEXT,
                definition_id TEXT, definition_version TEXT, status TEXT DEFAULT 'CURRENT'
            )
            """
        )
        db.executemany(
            """
            INSERT INTO derived_features (
                id,feature_name,scope_type,scope_key,local_date,value_num,value_code,unit,
                provider,source_sid,source_class,timezone_name,timezone_offset_seconds,
                coverage_status,attributes_json,definition_id,definition_version,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT')
            """,
            rows,
        )
        db.commit()


def _create_synthetic_l4(path, series_rows, baseline_rows):
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            """
            CREATE TABLE baseline_series (
                id INTEGER PRIMARY KEY, series_key TEXT, feature_name TEXT, scope_type TEXT,
                provider TEXT, source_sid TEXT, source_class TEXT, timezone_name TEXT,
                timezone_offset_seconds INTEGER, unit TEXT, observation_semantics TEXT,
                definition_id TEXT, definition_version TEXT, status TEXT DEFAULT 'CURRENT',
                created_at_utc TEXT, updated_at_utc TEXT
            )
            """
        )
        db.execute(
            """
            CREATE TABLE rolling_baselines (
                id INTEGER PRIMARY KEY, series_id INTEGER, window_days INTEGER, as_of_date TEXT,
                observation_count INTEGER, distinct_observation_dates INTEGER, history_span_days INTEGER,
                calendar_coverage REAL, mean REAL, median REAL, mad REAL, q10 REAL, q25 REAL,
                q50 REAL, q75 REAL, q90 REAL, unit TEXT, maturity TEXT,
                maturity_definition_id TEXT, maturity_definition_version TEXT,
                window_definition_id TEXT, window_definition_version TEXT,
                status TEXT DEFAULT 'CURRENT', attributes_json TEXT, created_at_utc TEXT, updated_at_utc TEXT
            )
            """
        )
        db.executemany(
            """
            INSERT INTO baseline_series (
                id,series_key,feature_name,scope_type,provider,source_sid,source_class,
                timezone_name,timezone_offset_seconds,unit,observation_semantics,
                definition_id,definition_version,status,created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT','','')
            """,
            series_rows,
        )
        db.executemany(
            """
            INSERT INTO rolling_baselines (
                id,series_id,window_days,as_of_date,observation_count,distinct_observation_dates,
                history_span_days,calendar_coverage,mean,median,mad,q10,q25,q50,q75,q90,unit,
                maturity,maturity_definition_id,maturity_definition_version,window_definition_id,
                window_definition_version,status,attributes_json,created_at_utc,updated_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT','{}','','')
            """,
            baseline_rows,
        )
        db.commit()


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.l3 = Path(self.temp.name) / "l3.sqlite3"
        self.l4 = Path(self.temp.name) / "l4.sqlite3"
        self.l5 = Path(self.temp.name) / "l5.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def _migrate_l5(self):
        subprocess.run(
            [sys.executable, str(APPLY_MIGRATIONS), "--l5", str(self.l5), "--migrations-root", str(ROOT / "migrations")],
            check=True, capture_output=True, text=True,
        )

    def _materialize(self, mode, l5=None):
        cmd = [
            sys.executable, str(MATERIALIZER), "--mode", mode,
            "--l3", str(self.l3), "--l4", str(self.l4), "--l5", str(l5 or self.l5),
        ]
        for key in ("deviation", "persistence", "trend", "change", "relationship", "evidence"):
            cmd += [f"--{key}", str(DEFS[key])]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def _baseline_row(self, bid, series_id, window, as_of, median, mad, q10, q25, q50, q75, q90, maturity="ESTABLISHED"):
        return (
            bid, series_id, window, as_of, 5, 5, 5, 1.0, median, median, mad,
            q10, q25, q50, q75, q90, "bpm", maturity, "l4d.baseline.maturity", "0.1",
            "l4c.baseline.windows", "0.1",
        )

    def test_no_lookahead_and_idempotent(self):
        scope = json.dumps({"local_date": "2026-08-10"}, sort_keys=True)
        _create_synthetic_l3(self.l3, [
            (1, "heart_rate.daily.mean", "DAILY", scope, "2026-08-10", 60.0, None, "bpm", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
            (2, "heart_rate.daily.mean", "DAILY", scope, "2026-08-11", 70.0, None, "bpm", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
            (3, "heart_rate.daily.mean", "DAILY", scope, "2026-08-12", 80.0, None, "bpm", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
        ])
        sk = json.dumps({"feature_name": "heart_rate.daily.mean", "provider": "xiaomi", "scope_type": "DAILY", "source_class": "NUMERIC_SOURCE", "source_sid": "sid", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "bpm"}, sort_keys=True)
        _create_synthetic_l4(self.l4, [
            (1, sk, "heart_rate.daily.mean", "DAILY", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "bpm", "DAILY_VALUE", "l4b.baseline.series", "0.1"),
        ], [
            self._baseline_row(1, 1, 7, "2026-08-11", 60.0, 5, 40, 45, 60, 65, 70),
            self._baseline_row(2, 1, 7, "2026-08-12", 70.0, 5, 50, 55, 70, 75, 80),
            self._baseline_row(3, 1, 7, "2026-08-13", 80.0, 5, 60, 65, 80, 85, 90),
        ])
        self._migrate_l5()
        first = self._materialize("full")
        second = self._materialize("full")
        self.assertEqual(second["inserted"]["deviation"], 0)
        self.assertGreater(first["inserted"]["deviation"], 0)

        with closing(sqlite3.connect(self.l5)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT absolute_deviation FROM deviation_analytics d
                JOIN analytics_series s ON s.id=d.series_id
                WHERE d.feature_date='2026-08-12' AND d.window_days=7 AND d.status='CURRENT'
                """
            ).fetchone()
        # 80 vs baseline as_of 08-12 (median 70) => +10, not the 08-13 baseline (median 80 => 0)
        self.assertEqual(row["absolute_deviation"], 10.0)

    def test_source_isolation_and_unit_mismatch(self):
        scope = json.dumps({"local_date": "2026-08-10"}, sort_keys=True)
        _create_synthetic_l3(self.l3, [
            (1, "steps.daily.sum", "DAILY", scope, "2026-08-10", 100.0, None, "steps", "xiaomi", "sid-a", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", "{}", "l3c.features.daily", "0.1"),
            (2, "steps.daily.sum", "DAILY", scope, "2026-08-10", 500.0, None, "steps", "xiaomi", "sid-b", "XIAOMI_GENERATED", "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", "{}", "l3c.features.daily", "0.1"),
            (3, "steps.daily.sum", "DAILY", scope, "2026-08-10", 100.0, None, "meters", "xiaomi", "sid-a", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", "{}", "l3c.features.daily", "0.1"),
        ])
        sk_a = json.dumps({"feature_name": "steps.daily.sum", "provider": "xiaomi", "scope_type": "DAILY", "source_class": "NUMERIC_SOURCE", "source_sid": "sid-a", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "steps"}, sort_keys=True)
        sk_b = json.dumps({"feature_name": "steps.daily.sum", "provider": "xiaomi", "scope_type": "DAILY", "source_class": "XIAOMI_GENERATED", "source_sid": "sid-b", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "steps"}, sort_keys=True)
        sk_m = json.dumps({"feature_name": "steps.daily.sum", "provider": "xiaomi", "scope_type": "DAILY", "source_class": "NUMERIC_SOURCE", "source_sid": "sid-a", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "meters"}, sort_keys=True)
        _create_synthetic_l4(self.l4, [
            (1, sk_a, "steps.daily.sum", "DAILY", "xiaomi", "sid-a", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "steps", "DAILY_VALUE", "l4b.baseline.series", "0.1"),
            (2, sk_b, "steps.daily.sum", "DAILY", "xiaomi", "sid-b", "XIAOMI_GENERATED", "Asia/Shanghai", 28800, "steps", "DAILY_VALUE", "l4b.baseline.series", "0.1"),
            (3, sk_m, "steps.daily.sum", "DAILY", "xiaomi", "sid-a", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "meters", "DAILY_VALUE", "l4b.baseline.series", "0.1"),
        ], [
            self._baseline_row(1, 1, 7, "2026-08-10", 50.0, 5, 40, 45, 50, 55, 60),
            self._baseline_row(2, 2, 7, "2026-08-10", 50.0, 5, 40, 45, 50, 55, 60),
            self._baseline_row(3, 3, 7, "2026-08-10", 50.0, 5, 40, 45, 50, 55, 60),
        ])
        self._migrate_l5()
        self._materialize("full")
        with closing(sqlite3.connect(self.l5)) as db:
            db.row_factory = sqlite3.Row
            n = db.execute("SELECT COUNT(*) n FROM analytics_series WHERE status='CURRENT'").fetchone()["n"]
        self.assertEqual(n, 3)

    def test_mad_zero_end_to_end(self):
        scope = json.dumps({"local_date": "2026-08-10"}, sort_keys=True)
        _create_synthetic_l3(self.l3, [
            (1, "spo2.daily.mean", "DAILY", scope, "2026-08-10", 99.0, None, "percent", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
        ])
        sk = json.dumps({"feature_name": "spo2.daily.mean", "provider": "xiaomi", "scope_type": "DAILY", "source_class": "NUMERIC_SOURCE", "source_sid": "sid", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "percent"}, sort_keys=True)
        _create_synthetic_l4(self.l4, [
            (1, sk, "spo2.daily.mean", "DAILY", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "percent", "DAILY_VALUE", "l4b.baseline.series", "0.1"),
        ], [
            self._baseline_row(1, 1, 7, "2026-08-10", 98.0, 0, 98, 98, 98, 98, 98),
        ])
        self._migrate_l5()
        self._materialize("full")
        with closing(sqlite3.connect(self.l5)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT robust_standardized_deviation, robust_z_unavailable_reason FROM deviation_analytics WHERE status='CURRENT'").fetchone()
        self.assertIsNone(row["robust_standardized_deviation"])
        self.assertEqual(row["robust_z_unavailable_reason"], "MAD_ZERO")

    def test_sleep_episode_no_persistence_trend_change(self):
        scope = json.dumps({"episode_start_utc": "t", "local_date": "2026-08-10"}, sort_keys=True)
        _create_synthetic_l3(self.l3, [
            (1, "sleep_source_episode.duration_seconds", "SOURCE_EPISODE", scope, "2026-08-10", 28000.0, None, "seconds", "xiaomi", "sid", "XIAOMI_GENERATED", "Asia/Shanghai", 28800, "VENDOR_INFERENCE", "{}", "l3c.features.daily", "0.1"),
        ])
        sk = json.dumps({"feature_name": "sleep_source_episode.duration_seconds", "provider": "xiaomi", "scope_type": "SOURCE_EPISODE", "source_class": "XIAOMI_GENERATED", "source_sid": "sid", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "seconds"}, sort_keys=True)
        _create_synthetic_l4(self.l4, [
            (1, sk, "sleep_source_episode.duration_seconds", "SOURCE_EPISODE", "xiaomi", "sid", "XIAOMI_GENERATED", "Asia/Shanghai", 28800, "seconds", "SOURCE_EPISODE_VALUE", "l4b.baseline.series", "0.1"),
        ], [
            self._baseline_row(1, 1, 7, "2026-08-10", 28000.0, 0, 28000, 28000, 28000, 28000, 28000),
        ])
        self._migrate_l5()
        result = self._materialize("full")
        self.assertEqual(result["desired"]["persistence"], 0)
        self.assertEqual(result["desired"]["trend"], 0)
        self.assertEqual(result["desired"]["change"], 0)
        self.assertGreater(result["desired"]["deviation"], 0)

    def test_revision_equals_full_and_no_l6_leakage(self):
        scope = json.dumps({"local_date": "2026-08-10"}, sort_keys=True)
        _create_synthetic_l3(self.l3, [
            (1, "heart_rate.daily.mean", "DAILY", scope, "2026-08-10", 60.0, None, "bpm", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
            (2, "heart_rate.daily.mean", "DAILY", scope, "2026-08-11", 70.0, None, "bpm", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
            (3, "heart_rate.daily.mean", "DAILY", scope, "2026-08-12", 80.0, None, "bpm", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "OBSERVED_ONLY", "{}", "l3c.features.daily", "0.1"),
        ])
        sk = json.dumps({"feature_name": "heart_rate.daily.mean", "provider": "xiaomi", "scope_type": "DAILY", "source_class": "NUMERIC_SOURCE", "source_sid": "sid", "timezone_name": "Asia/Shanghai", "timezone_offset_seconds": 28800, "unit": "bpm"}, sort_keys=True)
        _create_synthetic_l4(self.l4, [
            (1, sk, "heart_rate.daily.mean", "DAILY", "xiaomi", "sid", "NUMERIC_SOURCE", "Asia/Shanghai", 28800, "bpm", "DAILY_VALUE", "l4b.baseline.series", "0.1"),
        ], [
            self._baseline_row(1, 1, 7, "2026-08-11", 60.0, 5, 40, 45, 60, 65, 70),
            self._baseline_row(2, 1, 7, "2026-08-12", 70.0, 5, 50, 55, 70, 75, 80),
            self._baseline_row(3, 1, 7, "2026-08-13", 80.0, 5, 60, 65, 80, 85, 90),
        ])
        self._migrate_l5()
        self._materialize("full")
        with closing(sqlite3.connect(self.l3)) as db:
            db.execute("UPDATE derived_features SET value_num=99 WHERE id=2")
            db.commit()
        self._materialize("incremental")

        # fresh full rebuild from the revised L3
        l5b = Path(self.temp.name) / "l5b.sqlite3"
        subprocess.run(
            [sys.executable, str(APPLY_MIGRATIONS), "--l5", str(l5b), "--migrations-root", str(ROOT / "migrations")],
            check=True, capture_output=True, text=True,
        )
        self._materialize("full", l5=l5b)

        def sig(path):
            with closing(sqlite3.connect(path)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    "SELECT feature_date, current_value, absolute_deviation, deviation_class FROM deviation_analytics WHERE status='CURRENT' ORDER BY feature_date, l3_feature_id"
                ).fetchall()
                return [tuple(r) for r in rows]
        self.assertEqual(sig(self.l5), sig(l5b))

        # no L6 leakage
        with closing(sqlite3.connect(self.l5)) as db:
            names = [r[0] for r in db.execute("SELECT feature_name FROM analytics_series")]
            classes = [r[0] for r in db.execute("SELECT DISTINCT deviation_class FROM deviation_analytics")]
            classes += [r[0] for r in db.execute("SELECT DISTINCT trend_class FROM trend_analytics")]
            classes += [r[0] for r in db.execute("SELECT DISTINCT relationship_class FROM relationship_analytics")]
        forbidden = ("anomaly", "diagnos", "risk", "causal", "recommend", "readiness", "recovery", "score")
        self.assertFalse(any(t in n.lower() for t in forbidden for n in names))
        self.assertFalse(any("score" in c.lower() or "causal" in c.lower() for c in classes))


class ProductionDataTests(unittest.TestCase):
    @unittest.skipUnless(L3_PRODUCTION.exists() and L4_PRODUCTION.exists(), "production upstreams not present")
    def test_production_honest_and_no_lookahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            l5 = Path(tmp) / "l5.sqlite3"
            subprocess.run(
                [sys.executable, str(APPLY_MIGRATIONS), "--l5", str(l5), "--migrations-root", str(ROOT / "migrations")],
                check=True, capture_output=True, text=True,
            )
            cmd = [
                sys.executable, str(MATERIALIZER), "--mode", "full",
                "--l3", str(L3_PRODUCTION), "--l4", str(L4_PRODUCTION), "--l5", str(l5),
            ]
            for key in ("deviation", "persistence", "trend", "change", "relationship", "evidence"):
                cmd += [f"--{key}", str(DEFS[key])]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            with closing(sqlite3.connect(l5)) as db:
                db.row_factory = sqlite3.Row
                leak = db.execute(
                    """
                    SELECT COUNT(*) n FROM deviation_analytics d
                    JOIN analytics_baseline_inputs b ON b.analytic_type='DEVIATION' AND b.analytic_id=d.id
                    WHERE d.status='CURRENT' AND b.l4_baseline_as_of_date <> d.feature_date
                    """
                ).fetchone()["n"]
                change = {r[0] for r in db.execute("SELECT DISTINCT change_class FROM change_point_analytics WHERE status='CURRENT'")}
                pos = db.execute(
                    """
                    SELECT COUNT(*) n FROM relationship_analytics WHERE status='CURRENT' AND relationship_class='POSITIVE_ASSOCIATION'
                    """
                ).fetchone()["n"]
            self.assertEqual(leak, 0)
            self.assertEqual(change, {"INSUFFICIENT_EVIDENCE"})
            self.assertGreaterEqual(pos, 1)


if __name__ == "__main__":
    unittest.main()
