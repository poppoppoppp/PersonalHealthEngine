import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
PRODUCTION_L2 = Path(
    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3"
)
PRODUCTION_L3 = ROOT / "db" / "personal_health_features.sqlite3"
RUNNER = ROOT / "scripts" / "l3_sleep_incremental_runner_v0_1.py"
FULL_RUNNER = ROOT / "scripts" / "l3_sleep_full_runner_v0_1.py"
DEFINITION = ROOT / "definitions" / "normalizers" / "sleep_v0_1.json"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_backup(source, destination):
    uri = "file:" + source.as_posix() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)


class SleepIncrementalAcceptance(unittest.TestCase):
    def setUp(self):
        self.production_hashes = {
            PRODUCTION_L2: sha256(PRODUCTION_L2),
            PRODUCTION_L3: sha256(PRODUCTION_L3),
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.l2 = root / "l2.sqlite3"
        self.l3 = root / "l3.sqlite3"
        sqlite_backup(PRODUCTION_L2, self.l2)
        sqlite_backup(PRODUCTION_L3, self.l3)
        self.logical_id = None
        self.latest_version_id = None

    def tearDown(self):
        self.temp_dir.cleanup()
        for path, expected in self.production_hashes.items():
            self.assertEqual(sha256(path), expected)

    @staticmethod
    def payload(items):
        inner = {
            "bedtime": 1_800_000_000,
            "wake_up_time": 1_800_003_600,
            "device_bedtime": 1_800_000_000,
            "device_wake_up_time": 1_800_003_600,
            "duration": 60,
            "items": items,
        }
        return json.dumps(
            {
                "key": "sleep",
                "sid": "hlth.gen_test_sleep",
                "time": 1_800_003_600,
                "update_time": 1_800_003_601,
                "value": json.dumps(inner, separators=(",", ":")),
            },
            separators=(",", ":"),
        )

    def insert_new(self):
        raw_json = self.payload(
            [{"start_time": 1_800_000_000, "end_time": 1_800_003_600, "state": 6}]
        )
        with closing(sqlite3.connect(self.l2)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            artifact = db.execute(
                """
                SELECT o.source_artifact_id, o.ingestion_run_id, o.capture_id
                FROM raw_record_observations o
                ORDER BY o.id DESC LIMIT 1
                """
            ).fetchone()
            cursor = db.execute(
                """
                INSERT INTO logical_records (
                    provider, region, dataset, raw_key, raw_sid, raw_time,
                    identity_version, logical_key, first_seen_at_utc,
                    last_seen_at_utc, created_by_run_id
                ) VALUES ('xiaomi','cn','sleep','sleep','hlth.gen_test_sleep',
                          1800003600,'xiaomi-v0.1','synthetic-sleep-logical-key',
                          '2027-01-15T09:00:00+00:00','2027-01-15T09:00:00+00:00',?)
                """,
                (artifact[1],),
            )
            self.logical_id = cursor.lastrowid
            cursor = db.execute(
                """
                INSERT INTO raw_record_versions (
                    logical_record_id, payload_sha256, raw_json, raw_update_time,
                    zone_name, zone_offset, first_seen_at_utc, last_seen_at_utc,
                    first_seen_run_id
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.logical_id,
                    hashlib.sha256(raw_json.encode()).hexdigest(),
                    raw_json,
                    1_800_003_601,
                    "Asia/Shanghai",
                    28_800,
                    "2027-01-15T09:00:00+00:00",
                    "2027-01-15T09:00:00+00:00",
                    artifact[1],
                ),
            )
            self.latest_version_id = cursor.lastrowid
            observation_id = self.insert_observation(
                db, artifact, self.latest_version_id, "NEW"
            )
            db.commit()
        return observation_id

    def insert_revision(self):
        raw_json = self.payload(
            [
                {"start_time": 1_800_000_000, "end_time": 1_800_001_800, "state": 6},
                {"start_time": 1_800_001_800, "end_time": 1_800_001_800, "state": 5},
                {"start_time": 1_800_001_800, "end_time": 1_800_003_600, "state": 6},
            ]
        )
        with closing(sqlite3.connect(self.l2)) as db:
            artifact = db.execute(
                """
                SELECT o.source_artifact_id, o.ingestion_run_id, o.capture_id
                FROM raw_record_observations o ORDER BY o.id DESC LIMIT 1
                """
            ).fetchone()
            cursor = db.execute(
                """
                INSERT INTO raw_record_versions (
                    logical_record_id, payload_sha256, raw_json, raw_update_time,
                    zone_name, zone_offset, first_seen_at_utc, last_seen_at_utc,
                    first_seen_run_id
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    self.logical_id,
                    hashlib.sha256(raw_json.encode()).hexdigest(),
                    raw_json,
                    1_800_003_602,
                    "Asia/Shanghai",
                    28_800,
                    "2027-01-15T09:01:00+00:00",
                    "2027-01-15T09:01:00+00:00",
                    artifact[1],
                ),
            )
            self.latest_version_id = cursor.lastrowid
            observation_id = self.insert_observation(
                db, artifact, self.latest_version_id, "REVISION"
            )
            db.commit()
        return observation_id

    def insert_reobservation(self):
        with closing(sqlite3.connect(self.l2)) as db:
            artifact = db.execute(
                """
                SELECT o.source_artifact_id, o.ingestion_run_id, o.capture_id
                FROM raw_record_observations o ORDER BY o.id DESC LIMIT 1
                """
            ).fetchone()
            observation_id = self.insert_observation(
                db, artifact, self.latest_version_id, "REOBSERVATION"
            )
            db.commit()
        return observation_id

    @staticmethod
    def insert_observation(db, artifact, version_id, classification):
        ordinal = db.execute(
            "SELECT COALESCE(MAX(source_record_ordinal),0)+1 FROM raw_record_observations WHERE source_artifact_id=?",
            (artifact[0],),
        ).fetchone()[0]
        cursor = db.execute(
            """
            INSERT INTO raw_record_observations (
                source_artifact_id, source_record_ordinal, raw_record_version_id,
                ingestion_run_id, capture_id, ingested_at_utc, classification,
                late_arrival, dataset, provider, region
            ) VALUES (?,?,?,?,?,'2027-01-15T09:02:00+00:00',?,0,'sleep','xiaomi','cn')
            """,
            (artifact[0], ordinal, version_id, artifact[1], artifact[2], classification),
        )
        return cursor.lastrowid

    def run_incremental(self):
        result = subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--l2",
                str(self.l2),
                "--l3",
                str(self.l3),
                "--definition",
                str(DEFINITION),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def run_full(self):
        result = subprocess.run(
            [
                sys.executable,
                str(FULL_RUNNER),
                "--l2",
                str(self.l2),
                "--l3",
                str(self.l3),
                "--definition",
                str(DEFINITION),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def fact_counts(self):
        with closing(sqlite3.connect(self.l3)) as db:
            return dict(
                db.execute(
                    """
                    SELECT fr.status, COUNT(*)
                    FROM fact_registry fr
                    JOIN fact_provenance fp ON fp.fact_id=fr.id
                    WHERE fr.definition_id='normalize.sleep'
                      AND fp.l2_logical_record_id=?
                    GROUP BY fr.status
                    """,
                    (self.logical_id,),
                ).fetchall()
            )

    def checkpoint(self):
        with closing(sqlite3.connect(self.l3)) as db:
            return db.execute(
                "SELECT last_l2_observation_id FROM processing_checkpoints WHERE pipeline_name='normalize.sleep'"
            ).fetchone()[0]

    def test_new_materializes_complete_fact_set(self):
        observation_id = self.insert_new()
        self.run_incremental()
        self.assertEqual(self.fact_counts(), {"CURRENT": 2})
        self.assertEqual(self.checkpoint(), observation_id)

    def test_full_runner_rebuilds_sleep_and_is_idempotent(self):
        with closing(sqlite3.connect(self.l3)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='derived_features'"
            ).fetchone():
                db.execute("DELETE FROM derived_features")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quality_assessments'"
            ).fetchone():
                db.execute("DELETE FROM quality_assessments")
                db.execute("DELETE FROM source_resolution_decisions")
            db.execute(
                "DELETE FROM fact_registry WHERE definition_id='normalize.sleep'"
            )
            db.execute(
                "DELETE FROM processing_checkpoints WHERE pipeline_name='normalize.sleep'"
            )
            db.execute(
                "DELETE FROM definition_registry WHERE definition_id='normalize.sleep'"
            )
            db.commit()
        first = self.run_full()
        second = self.run_full()
        self.assertEqual(first["current_facts"], 163)
        self.assertEqual(first["inserted"], 163)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["skipped"], 163)

    def test_revision_stales_complete_old_set_and_rebuilds_complete_new_set(self):
        self.insert_new()
        self.run_incremental()
        observation_id = self.insert_revision()
        self.run_incremental()
        self.assertEqual(self.fact_counts(), {"CURRENT": 4, "STALE": 2})
        self.assertEqual(self.checkpoint(), observation_id)

    def test_reobservation_is_business_no_op_and_advances_checkpoint(self):
        self.insert_new()
        self.run_incremental()
        before = self.fact_counts()
        observation_id = self.insert_reobservation()
        self.run_incremental()
        self.assertEqual(self.fact_counts(), before)
        self.assertEqual(self.checkpoint(), observation_id)


if __name__ == "__main__":
    unittest.main()
