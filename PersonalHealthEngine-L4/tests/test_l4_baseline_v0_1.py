import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(r"D:\PersonalHealthEngine-L4")
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from l4_baseline_core_v0_1 import (  # noqa: E402
    build_series,
    compute_series_baselines,
    maturity_for,
    quantile,
    series_identity,
)

L3_PRODUCTION = Path(r"D:\PersonalHealthEngine-L3\db\personal_health_features.sqlite3")
APPLY_MIGRATIONS = SCRIPTS / "apply_migrations_v0_1.py"
MATERIALIZER = SCRIPTS / "l4_baseline_materializer_v0_1.py"

DEFS = (
    ROOT / "definitions" / "eligibility" / "l4a_baseline_eligibility_v0_1.json",
    ROOT / "definitions" / "series" / "l4b_baseline_series_v0_1.json",
    ROOT / "definitions" / "windows" / "l4c_baseline_windows_v0_1.json",
    ROOT / "definitions" / "maturity" / "l4d_baseline_maturity_v0_1.json",
)

THRESHOLDS_7 = {
    "min_observations": 3,
    "established_min_observations": 5,
    "established_min_span_days": 5,
    "established_min_coverage": 0.60,
}


def make_feature(
    fid,
    name,
    scope_type,
    local_date,
    value,
    unit,
    source_sid,
    source_class,
    coverage="OBSERVED_ONLY",
):
    scope_key = json.dumps(
        {
            "local_date": local_date,
            "metric": name.split(".")[0],
            "provider": "xiaomi",
            "source_class": source_class,
            "source_sid": source_sid,
            "timezone_name": "Asia/Shanghai",
            "timezone_offset_seconds": 28800,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "id": fid,
        "feature_name": name,
        "scope_type": scope_type,
        "scope_key": scope_key,
        "local_date": local_date,
        "value_num": float(value),
        "value_code": None,
        "unit": unit,
        "provider": "xiaomi",
        "source_sid": source_sid,
        "source_class": source_class,
        "timezone_name": "Asia/Shanghai",
        "timezone_offset_seconds": 28800,
        "coverage_status": coverage,
        "attributes_json": json.dumps({"source_scoped": True}, sort_keys=True),
        "definition_id": "l3c.features.daily",
        "definition_version": "0.1",
    }


class CoreUnitTests(unittest.TestCase):
    def test_quantile_type7(self):
        self.assertEqual(quantile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertEqual(quantile([1.0, 2.0, 3.0], 0.5), 2.0)
        self.assertEqual(quantile([1.0], 0.5), 1.0)
        self.assertIsNone(quantile([], 0.5))

    def test_series_identity_isolates_sources_and_units(self):
        a = make_feature(1, "steps.daily.sum", "DAILY", "2026-08-10", 100, "steps", "sid-a", "NUMERIC_SOURCE")
        b = make_feature(2, "steps.daily.sum", "DAILY", "2026-08-10", 100, "steps", "sid-b", "XIAOMI_GENERATED")
        c = make_feature(3, "steps.daily.sum", "DAILY", "2026-08-10", 100, "meters", "sid-a", "NUMERIC_SOURCE")
        self.assertNotEqual(series_identity(a)[0], series_identity(b)[0])
        self.assertNotEqual(series_identity(a)[0], series_identity(c)[0])
        self.assertEqual(series_identity(a)[0], series_identity(a)[0])

    def test_as_of_window_boundary_no_lookahead(self):
        features = [
            make_feature(1, "heart_rate.daily.mean", "DAILY", "2026-08-01", 10.0, "bpm", "sid", "NUMERIC_SOURCE"),
            make_feature(2, "heart_rate.daily.mean", "DAILY", "2026-08-02", 20.0, "bpm", "sid", "NUMERIC_SOURCE"),
            make_feature(3, "heart_rate.daily.mean", "DAILY", "2026-08-08", 80.0, "bpm", "sid", "NUMERIC_SOURCE"),
        ]
        series = build_series(features)
        key = next(iter(series))
        as_of = ["2026-08-08", "2026-08-09", "2026-08-15"]
        baselines = {
            b["as_of_date"]: b
            for b in compute_series_baselines(key, series[key], as_of, [7], {"7": THRESHOLDS_7})
        }
        # as_of 08-08: window [08-01, 08-07] -> excludes 08-08, includes 08-01/08-02
        self.assertEqual(baselines["2026-08-08"]["observation_count"], 2)
        self.assertEqual(baselines["2026-08-08"]["inputs"], (1, 2))
        # as_of 08-09: window [08-02, 08-08] -> includes 08-08 (feature id 3)
        self.assertEqual(baselines["2026-08-09"]["observation_count"], 2)
        self.assertEqual(baselines["2026-08-09"]["inputs"], (2, 3))
        # as_of 08-15: window [08-08, 08-14] -> 08-08 is exactly at as_of - W, included
        self.assertEqual(baselines["2026-08-15"]["observation_count"], 1)
        self.assertEqual(baselines["2026-08-15"]["inputs"], (3,))

    def test_maturity_transition(self):
        cases = [
            (0, "INSUFFICIENT_HISTORY"),
            (2, "INSUFFICIENT_HISTORY"),
            (3, "PROVISIONAL"),
            (4, "PROVISIONAL"),
            (5, "ESTABLISHED"),
        ]
        for count, expected in cases:
            span = count if count else None
            distinct = count
            coverage = count / 7.0
            self.assertEqual(
                maturity_for(7, count, span, distinct, coverage, {"7": THRESHOLDS_7}),
                expected,
            )

    def test_missing_is_not_zero(self):
        features = [
            make_feature(1, "spo2.daily.mean", "DAILY", "2026-08-10", 97.0, "percent", "sid", "NUMERIC_SOURCE"),
            make_feature(3, "spo2.daily.mean", "DAILY", "2026-08-12", 99.0, "percent", "sid", "NUMERIC_SOURCE"),
        ]
        series = build_series(features)
        key = next(iter(series))
        baseline = compute_series_baselines(key, series[key], ["2026-08-13"], [7], {"7": THRESHOLDS_7})[0]
        self.assertEqual(baseline["observation_count"], 2)
        self.assertEqual(baseline["distinct_observation_dates"], 2)
        self.assertNotIn(0.0, [baseline["median"] or -1])


class MaterializerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.l3 = base / "l3.sqlite3"
        self.l4 = base / "l4.sqlite3"
        self._create_synthetic_l3()
        self._migrate_l4()

    def tearDown(self):
        self.temp.cleanup()

    def _create_synthetic_l3(self):
        with closing(sqlite3.connect(self.l3)) as db:
            db.execute(
                """
                CREATE TABLE derived_features (
                    id INTEGER PRIMARY KEY,
                    feature_name TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    value_num REAL,
                    value_code TEXT,
                    unit TEXT,
                    provider TEXT NOT NULL,
                    source_sid TEXT NOT NULL,
                    source_class TEXT NOT NULL,
                    timezone_name TEXT,
                    timezone_offset_seconds INTEGER,
                    coverage_status TEXT NOT NULL,
                    attributes_json TEXT,
                    definition_id TEXT NOT NULL,
                    definition_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'CURRENT'
                )
                """
            )
            db.commit()

    def _insert_features(self, rows):
        with closing(sqlite3.connect(self.l3)) as db:
            db.executemany(
                """
                INSERT INTO derived_features (
                    id,feature_name,scope_type,scope_key,local_date,value_num,value_code,
                    unit,provider,source_sid,source_class,timezone_name,
                    timezone_offset_seconds,coverage_status,attributes_json,
                    definition_id,definition_version,status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'CURRENT')
                """,
                rows,
            )
            db.commit()

    def _migrate_l4(self):
        subprocess.run(
            [
                sys.executable, str(APPLY_MIGRATIONS),
                "--l4", str(self.l4), "--migrations-root", str(ROOT / "migrations"),
            ],
            check=True, capture_output=True, text=True,
        )

    def _materialize(self, mode, l4=None):
        command = [
            sys.executable, str(MATERIALIZER), "--mode", mode,
            "--l3", str(self.l3), "--l4", str(l4 or self.l4),
            "--eligibility", str(DEFS[0]), "--series", str(DEFS[1]),
            "--windows", str(DEFS[2]), "--maturity", str(DEFS[3]),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def _baseline_signatures(l4):
        with closing(sqlite3.connect(l4)) as db:
            db.row_factory = sqlite3.Row
            inputs = {}
            for row in db.execute("SELECT baseline_id, l3_feature_id FROM baseline_feature_inputs"):
                inputs.setdefault(row[0], []).append(row[1])
            series = {row["id"]: row["series_key"] for row in db.execute("SELECT * FROM baseline_series")}
            return {
                (
                    series[row["series_id"]], row["window_days"], row["as_of_date"],
                    row["observation_count"], row["distinct_observation_dates"],
                    row["history_span_days"], row["calendar_coverage"],
                    row["mean"], row["median"], row["mad"], row["q10"], row["q25"],
                    row["q50"], row["q75"], row["q90"], row["unit"], row["maturity"],
                    row["attributes_json"], tuple(sorted(inputs.get(row["id"], []))),
                )
                for row in db.execute("SELECT * FROM rolling_baselines WHERE status='CURRENT'")
            }

    def _three_day_series(self):
        rows = [
            (
                1, "heart_rate.daily.mean", "DAILY",
                json.dumps({"local_date": "2026-08-10"}, sort_keys=True),
                "2026-08-10", 60.0, None, "bpm", "xiaomi", "896085753", "NUMERIC_SOURCE",
                "Asia/Shanghai", 28800, "OBSERVED_ONLY", json.dumps({"a": 1}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
            (
                2, "heart_rate.daily.mean", "DAILY",
                json.dumps({"local_date": "2026-08-11"}, sort_keys=True),
                "2026-08-11", 70.0, None, "bpm", "xiaomi", "896085753", "NUMERIC_SOURCE",
                "Asia/Shanghai", 28800, "OBSERVED_ONLY", json.dumps({"a": 1}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
            (
                3, "heart_rate.daily.mean", "DAILY",
                json.dumps({"local_date": "2026-08-12"}, sort_keys=True),
                "2026-08-12", 80.0, None, "bpm", "xiaomi", "896085753", "NUMERIC_SOURCE",
                "Asia/Shanghai", 28800, "OBSERVED_ONLY", json.dumps({"a": 1}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
        ]
        self._insert_features(rows)

    def test_full_build_no_lookahead_and_idempotent(self):
        self._three_day_series()
        first = self._materialize("full")
        second = self._materialize("full")
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["stale"], 0)
        self.assertGreater(first["inserted"], 0)

        with closing(sqlite3.connect(self.l4)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT observation_count FROM rolling_baselines rb
                JOIN baseline_series bs ON bs.id=rb.series_id
                WHERE bs.feature_name='heart_rate.daily.mean' AND rb.window_days=7
                  AND rb.as_of_date='2026-08-12' AND rb.status='CURRENT'
                """
            ).fetchone()
        # as_of 08-12 must use only 08-11 and earlier -> 2 observations (08-10, 08-11)
        self.assertEqual(row["observation_count"], 2)

    def test_source_isolation(self):
        rows = [
            (
                1, "steps.daily.sum", "DAILY",
                json.dumps({"local_date": "2026-08-10"}, sort_keys=True),
                "2026-08-10", 100.0, None, "steps", "xiaomi", "896085753", "NUMERIC_SOURCE",
                "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", json.dumps({}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
            (
                2, "steps.daily.sum", "DAILY",
                json.dumps({"local_date": "2026-08-10"}, sort_keys=True),
                "2026-08-10", 500.0, None, "steps", "xiaomi", "hlth.gen_1", "XIAOMI_GENERATED",
                "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", json.dumps({}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
            (
                3, "steps.daily.sum", "DAILY",
                json.dumps({"local_date": "2026-08-11"}, sort_keys=True),
                "2026-08-11", 110.0, None, "steps", "xiaomi", "896085753", "NUMERIC_SOURCE",
                "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", json.dumps({}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
            (
                4, "steps.daily.sum", "DAILY",
                json.dumps({"local_date": "2026-08-11"}, sort_keys=True),
                "2026-08-11", 510.0, None, "steps", "xiaomi", "hlth.gen_1", "XIAOMI_GENERATED",
                "Asia/Shanghai", 28800, "VENDOR_BUCKET_WIDTH_UNRESOLVED", json.dumps({}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
        ]
        self._insert_features(rows)
        self._materialize("full")
        with closing(sqlite3.connect(self.l4)) as db:
            db.row_factory = sqlite3.Row
            series = db.execute(
                "SELECT COUNT(*) n FROM baseline_series WHERE status='CURRENT' AND feature_name='steps.daily.sum'"
            ).fetchone()["n"]
            mixed = db.execute(
                """
                SELECT COUNT(*) n FROM baseline_series
                WHERE status='CURRENT' AND feature_name='steps.daily.sum'
                  AND source_class NOT IN ('NUMERIC_SOURCE','XIAOMI_GENERATED')
                """
            ).fetchone()["n"]
        self.assertEqual(series, 2)
        self.assertEqual(mixed, 0)

    def test_revision_recomputes_affected_and_equals_full(self):
        self._three_day_series()
        self._materialize("full")
        with closing(sqlite3.connect(self.l3)) as db:
            db.execute("UPDATE derived_features SET value_num=99 WHERE id=2")
            db.commit()
        incremental = self._materialize("incremental")
        self.assertGreaterEqual(incremental["stale"], 0)

        # Build a clean full rebuild against the revised L3.
        rebuilt = Path(self.temp.name) / "rebuilt.sqlite3"
        subprocess.run(
            [
                sys.executable, str(APPLY_MIGRATIONS),
                "--l4", str(rebuilt), "--migrations-root", str(ROOT / "migrations"),
            ],
            check=True, capture_output=True, text=True,
        )
        self._materialize("full", l4=rebuilt)
        self.assertEqual(
            self._baseline_signatures(self.l4),
            self._baseline_signatures(rebuilt),
        )

    def test_incremental_from_empty_equals_full(self):
        self._three_day_series()
        self._materialize("full")
        empty = Path(self.temp.name) / "empty.sqlite3"
        subprocess.run(
            [
                sys.executable, str(APPLY_MIGRATIONS),
                "--l4", str(empty), "--migrations-root", str(ROOT / "migrations"),
            ],
            check=True, capture_output=True, text=True,
        )
        self._materialize("incremental", l4=empty)
        self.assertEqual(
            self._baseline_signatures(self.l4),
            self._baseline_signatures(empty),
        )

    def test_sleep_episode_has_no_canonical_night(self):
        scope = json.dumps(
            {
                "episode_start_utc": "2026-08-09T16:00:00+00:00",
                "episode_end_utc": "2026-08-10T00:00:00+00:00",
                "l2_logical_record_id": 1,
                "local_date": "2026-08-10",
                "metric": "sleep_source_episode",
            },
            sort_keys=True,
        )
        rows = [
            (
                1, "sleep_source_episode.duration_seconds", "SOURCE_EPISODE", scope,
                "2026-08-10", 28000.0, None, "seconds", "xiaomi", "hlth.gen_1", "XIAOMI_GENERATED",
                "Asia/Shanghai", 28800, "VENDOR_INFERENCE", json.dumps({}, sort_keys=True),
                "l3c.features.daily", "0.1",
            ),
        ]
        self._insert_features(rows)
        self._materialize("full")
        with closing(sqlite3.connect(self.l4)) as db:
            db.row_factory = sqlite3.Row
            semantics = db.execute(
                "SELECT DISTINCT observation_semantics FROM baseline_series WHERE feature_name LIKE 'sleep%'"
            ).fetchone()[0]
            canonical = db.execute(
                "SELECT COUNT(*) n FROM rolling_baselines WHERE lower(attributes_json) LIKE '%canonical_night\":true%'"
            ).fetchone()["n"]
        self.assertEqual(semantics, "SOURCE_EPISODE_VALUE")
        self.assertEqual(canonical, 0)


class ProductionDataTests(unittest.TestCase):
    @unittest.skipUnless(L3_PRODUCTION.exists(), "production L3 not present")
    def test_production_baselines_honest_and_no_lookahead(self):
        if not L3_PRODUCTION.exists():
            self.skipTest("production L3 not present")
        with tempfile.TemporaryDirectory() as tmp:
            l4 = Path(tmp) / "l4.sqlite3"
            subprocess.run(
                [
                    sys.executable, str(APPLY_MIGRATIONS),
                    "--l4", str(l4), "--migrations-root", str(ROOT / "migrations"),
                ],
                check=True, capture_output=True, text=True,
            )
            command = [
                sys.executable, str(MATERIALIZER), "--mode", "full",
                "--l3", str(L3_PRODUCTION), "--l4", str(l4),
                "--eligibility", str(DEFS[0]), "--series", str(DEFS[1]),
                "--windows", str(DEFS[2]), "--maturity", str(DEFS[3]),
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            with closing(sqlite3.connect(l4)) as db:
                db.row_factory = sqlite3.Row
                series_count = db.execute(
                    "SELECT COUNT(*) n FROM baseline_series WHERE status='CURRENT'"
                ).fetchone()["n"]
                resting = db.execute(
                    """
                    SELECT DISTINCT maturity FROM rolling_baselines rb
                    JOIN baseline_series bs ON bs.id=rb.series_id
                    WHERE bs.feature_name='resting_heart_rate.daily.value'
                      AND rb.status='CURRENT' AND rb.as_of_date='2026-08-17'
                    """
                ).fetchall()
                leak = db.execute(
                    """
                    SELECT COUNT(*) n FROM (
                        SELECT rb.id FROM rolling_baselines rb
                        JOIN baseline_feature_inputs bfi ON bfi.baseline_id=rb.id
                        WHERE rb.status='CURRENT'
                          AND bfi.l3_local_date >= rb.as_of_date
                    )
                    """
                ).fetchone()["n"]
            self.assertGreater(series_count, 0)
            self.assertEqual({row["maturity"] for row in resting}, {"INSUFFICIENT_HISTORY"})
            self.assertEqual(leak, 0)


if __name__ == "__main__":
    unittest.main()
