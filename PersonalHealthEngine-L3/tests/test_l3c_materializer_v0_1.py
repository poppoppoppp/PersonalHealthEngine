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
L2_PRODUCTION = Path(r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3")
L3_PRODUCTION = ROOT / "db" / "personal_health_features.sqlite3"
MIGRATIONS = ROOT / "scripts" / "apply_migrations_v0_1.py"
L3B = ROOT / "scripts" / "l3b_materializer_v0_1.py"
L3C = ROOT / "scripts" / "l3c_materializer_v0_1.py"
FEATURE_DEFINITION = ROOT / "definitions" / "features" / "daily_features_v0_1.json"
QUALITY_DEFINITION = ROOT / "definitions" / "quality" / "l3b_structural_quality_v0_1.json"
RESOLUTION_DEFINITION = ROOT / "definitions" / "resolution" / "l3b_source_resolution_v0_1.json"


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


class L3CMaterializerTest(unittest.TestCase):
    def setUp(self):
        self.hashes = {
            L2_PRODUCTION: sha256(L2_PRODUCTION),
            L3_PRODUCTION: sha256(L3_PRODUCTION),
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        temp = Path(self.temp_dir.name)
        self.l2 = temp / "l2.sqlite3"
        self.l3 = temp / "l3.sqlite3"
        sqlite_backup(L2_PRODUCTION, self.l2)
        sqlite_backup(L3_PRODUCTION, self.l3)
        self.run_command(
            [
                sys.executable,
                str(MIGRATIONS),
                "--l3",
                str(self.l3),
                "--migrations-root",
                str(ROOT / "migrations"),
            ]
        )
        with closing(sqlite3.connect(self.l3)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            if self.table_exists(db, "derived_features"):
                db.execute("DELETE FROM derived_features")
                db.execute(
                    "DELETE FROM processing_checkpoints WHERE pipeline_name='l3c.derived_features'"
                )
                db.execute(
                    "DELETE FROM definition_registry WHERE definition_id='l3c.features.daily'"
                )
                db.commit()

    def tearDown(self):
        self.temp_dir.cleanup()
        for path, expected in self.hashes.items():
            self.assertEqual(sha256(path), expected)

    @staticmethod
    def table_exists(db, name):
        return bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
        )

    def run_command(self, command):
        result = subprocess.run(
            command, text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def run_l3b(self, database=None):
        return self.run_command(
            [
                sys.executable, str(L3B), "--mode", "incremental",
                "--l2", str(self.l2), "--l3", str(database or self.l3),
                "--quality-definition", str(QUALITY_DEFINITION),
                "--resolution-definition", str(RESOLUTION_DEFINITION),
            ]
        )

    @staticmethod
    def semantic_signatures(database):
        with closing(sqlite3.connect(database)) as db:
            db.row_factory = sqlite3.Row
            facts = {}
            for row in db.execute(
                "SELECT feature_id,fact_id,input_role FROM derived_feature_fact_inputs"
            ):
                facts.setdefault(row[0], []).append((row[1], row[2]))
            quality = {}
            for row in db.execute(
                "SELECT feature_id,assessment_id FROM derived_feature_quality_inputs"
            ):
                quality.setdefault(row[0], []).append(row[1])
            resolution = {}
            for row in db.execute(
                "SELECT feature_id,decision_id FROM derived_feature_resolution_inputs"
            ):
                resolution.setdefault(row[0], []).append(row[1])
            return {
                (
                    row["feature_name"], row["scope_type"], row["scope_key"],
                    row["local_date"], row["value_num"], row["value_code"],
                    row["unit"], row["sample_count"], row["provider"],
                    row["source_sid"], row["source_class"], row["timezone_name"],
                    row["timezone_offset_seconds"], row["coverage_status"],
                    row["attributes_json"], tuple(sorted(facts.get(row["id"], []))),
                    tuple(sorted(quality.get(row["id"], []))),
                    tuple(sorted(resolution.get(row["id"], []))),
                )
                for row in db.execute(
                    "SELECT * FROM derived_features WHERE status='CURRENT'"
                )
            }

    def run_l3c(self, mode="full", database=None):
        self.assertTrue(L3C.exists(), f"missing materializer: {L3C}")
        return self.run_command(
            [
                sys.executable, str(L3C), "--mode", mode,
                "--l2", str(self.l2), "--l3", str(database or self.l3),
                "--definition", str(FEATURE_DEFINITION),
            ]
        )

    def test_full_materialization_is_source_aware_provenance_complete_and_idempotent(self):
        first = self.run_l3c("full")
        second = self.run_l3c("full")
        with closing(sqlite3.connect(self.l3)) as db:
            features = db.execute(
                "SELECT COUNT(*) FROM derived_features WHERE status='CURRENT'"
            ).fetchone()[0]
            no_fact_input = db.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT f.id FROM derived_features f
                    LEFT JOIN derived_feature_fact_inputs i ON i.feature_id=f.id
                    WHERE f.status='CURRENT' GROUP BY f.id HAVING COUNT(i.fact_id)=0
                )
                """
            ).fetchone()[0]
            no_quality_input = db.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT f.id FROM derived_features f
                    LEFT JOIN derived_feature_quality_inputs i ON i.feature_id=f.id
                    WHERE f.status='CURRENT' GROUP BY f.id HAVING COUNT(i.assessment_id)=0
                )
                """
            ).fetchone()[0]
            no_resolution_input = db.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT f.id FROM derived_features f
                    LEFT JOIN derived_feature_resolution_inputs i ON i.feature_id=f.id
                    WHERE f.status='CURRENT' GROUP BY f.id HAVING COUNT(i.decision_id)=0
                )
                """
            ).fetchone()[0]
            multi_input = db.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT feature_id FROM derived_feature_fact_inputs
                    GROUP BY feature_id HAVING COUNT(DISTINCT fact_id)>1
                )
                """
            ).fetchone()[0]
            mixed_bucket_source = db.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT f.id,COUNT(DISTINCT x.source_class) n
                    FROM derived_features f
                    JOIN derived_feature_fact_inputs i ON i.feature_id=f.id
                    JOIN normalized_bucket_facts x ON x.fact_id=i.fact_id
                    WHERE f.status='CURRENT' AND f.feature_name IN (
                        'steps.daily.sum','calories.daily.sum'
                    ) GROUP BY f.id HAVING n>1
                )
                """
            ).fetchone()[0]
            forbidden = db.execute(
                """
                SELECT COUNT(*) FROM derived_features WHERE lower(feature_name) IN (
                    'health_score','readiness_score','recovery_score',
                    'sleep_score','overall_wellness_score'
                )
                """
            ).fetchone()[0]
            zero_samples = db.execute(
                "SELECT COUNT(*) FROM derived_features WHERE sample_count<1"
            ).fetchone()[0]
        self.assertGreater(features, 0)
        self.assertEqual((no_fact_input, no_quality_input, no_resolution_input), (0, 0, 0))
        self.assertGreater(multi_input, 0)
        self.assertEqual(mixed_bucket_source, 0)
        self.assertEqual(forbidden, 0)
        self.assertEqual(zero_samples, 0)
        self.assertGreater(first["inserted"], 0)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["stale"], 0)

    def replace_fact(self, metric, table, value_column):
        with closing(sqlite3.connect(self.l3)) as db:
            db.row_factory = sqlite3.Row
            old = db.execute(
                f"""
                SELECT fr.* FROM fact_registry fr JOIN {table} x ON x.fact_id=fr.id
                WHERE fr.status='CURRENT' AND fr.metric=? LIMIT 1
                """,
                (metric,),
            ).fetchone()
            db.execute("UPDATE fact_registry SET status='STALE' WHERE id=?", (old["id"],))
            cursor = db.execute(
                """
                INSERT INTO fact_registry (
                    fact_kind,metric,evidence_type,definition_id,definition_version,
                    status,created_at_utc,updated_at_utc
                ) VALUES (?,?,?,?,?,'CURRENT',?,?)
                """,
                (
                    old["fact_kind"], old["metric"], old["evidence_type"],
                    old["definition_id"], old["definition_version"],
                    old["created_at_utc"], old["updated_at_utc"],
                ),
            )
            new_id = cursor.lastrowid
            columns = [
                row[1]
                for row in db.execute(f"PRAGMA table_info({table})")
                if row[1] != "fact_id"
            ]
            names = ",".join(columns)
            db.execute(
                f"INSERT INTO {table} (fact_id,{names}) SELECT ?,{names} FROM {table} WHERE fact_id=?",
                (new_id, old["id"]),
            )
            if value_column == "value_code":
                db.execute(
                    f"UPDATE {table} SET value_code=CASE value_code WHEN 'AWAKE' THEN 'SLEEP' ELSE 'AWAKE' END WHERE fact_id=?",
                    (new_id,),
                )
            else:
                db.execute(
                    f"UPDATE {table} SET {value_column}={value_column}+1 WHERE fact_id=?",
                    (new_id,),
                )
            db.execute(
                """
                INSERT INTO fact_provenance
                SELECT ?,l2_logical_record_id,l2_raw_version_id,provenance_role,created_at_utc
                FROM fact_provenance WHERE fact_id=?
                """,
                (new_id, old["id"]),
            )
            db.commit()
        return old["id"], new_id

    def test_point_bucket_and_sleep_revisions_propagate(self):
        self.run_l3c("full")
        cases = (
            ("heart_rate", "normalized_point_facts", "value_num"),
            ("steps", "normalized_bucket_facts", "value_num"),
            ("sleep_vendor_stage_segment", "normalized_interval_facts", "value_code"),
        )
        for metric, table, value_column in cases:
            with self.subTest(metric=metric):
                old_id, new_id = self.replace_fact(metric, table, value_column)
                self.run_l3b()
                result = self.run_l3c("incremental")
                with closing(sqlite3.connect(self.l3)) as db:
                    old_current_links = db.execute(
                        """
                        SELECT COUNT(*) FROM derived_feature_fact_inputs i
                        JOIN derived_features f ON f.id=i.feature_id
                        WHERE f.status='CURRENT' AND i.fact_id=?
                        """,
                        (old_id,),
                    ).fetchone()[0]
                    new_current_links = db.execute(
                        """
                        SELECT COUNT(*) FROM derived_feature_fact_inputs i
                        JOIN derived_features f ON f.id=i.feature_id
                        WHERE f.status='CURRENT' AND i.fact_id=?
                        """,
                        (new_id,),
                    ).fetchone()[0]
                self.assertEqual(old_current_links, 0)
                self.assertGreater(new_current_links, 0)
                self.assertGreater(result["stale"], 0)

    def test_reobservation_is_business_no_op_and_advances_checkpoint(self):
        self.run_l3c("full")
        with closing(sqlite3.connect(self.l3)) as db:
            before = db.execute("SELECT COUNT(*) FROM derived_features").fetchone()[0]
        with closing(sqlite3.connect(self.l2)) as db:
            row = db.execute(
                """
                SELECT source_artifact_id,ingestion_run_id,capture_id,
                       raw_record_version_id,dataset,provider,region
                FROM raw_record_observations ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            ordinal = db.execute(
                "SELECT MAX(source_record_ordinal)+1 FROM raw_record_observations WHERE source_artifact_id=?",
                (row[0],),
            ).fetchone()[0]
            cursor = db.execute(
                """
                INSERT INTO raw_record_observations (
                    source_artifact_id,source_record_ordinal,raw_record_version_id,
                    ingestion_run_id,capture_id,ingested_at_utc,classification,
                    late_arrival,dataset,provider,region
                ) VALUES (?,?,?,?,?,'2027-01-15T11:00:00+00:00','REOBSERVATION',0,?,?,?)
                """,
                (row[0], ordinal, row[3], row[1], row[2], row[4], row[5], row[6]),
            )
            observation_id = cursor.lastrowid
            db.commit()
        self.run_l3b()
        result = self.run_l3c("incremental")
        with closing(sqlite3.connect(self.l3)) as db:
            after = db.execute("SELECT COUNT(*) FROM derived_features").fetchone()[0]
            checkpoint = db.execute(
                "SELECT last_l2_observation_id FROM processing_checkpoints WHERE pipeline_name='l3c.derived_features'"
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["stale"], 0)
        self.assertEqual(checkpoint, observation_id)

    def test_full_and_incremental_current_semantics_are_equivalent(self):
        self.run_l3c("full")
        comparison = Path(self.temp_dir.name) / "comparison.sqlite3"
        sqlite_backup(self.l3, comparison)
        for database in (self.l3, comparison):
            with closing(sqlite3.connect(database)) as db:
                fact_id = db.execute(
                    """
                    SELECT fr.id FROM fact_registry fr
                    JOIN normalized_point_facts x ON x.fact_id=fr.id
                    WHERE fr.status='CURRENT' AND fr.metric='heart_rate' LIMIT 1
                    """
                ).fetchone()[0]
                db.execute(
                    "UPDATE normalized_point_facts SET value_num=value_num+1 WHERE fact_id=?",
                    (fact_id,),
                )
                db.commit()
        self.run_l3b(self.l3)
        self.run_l3b(comparison)
        self.run_l3c("incremental", self.l3)
        self.run_l3c("full", comparison)
        self.assertEqual(
            self.semantic_signatures(self.l3),
            self.semantic_signatures(comparison),
        )


if __name__ == "__main__":
    unittest.main()
