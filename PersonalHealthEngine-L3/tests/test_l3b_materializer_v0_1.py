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
MIGRATION_RUNNER = ROOT / "scripts" / "apply_migrations_v0_1.py"
MATERIALIZER = ROOT / "scripts" / "l3b_materializer_v0_1.py"
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


class L3BMaterializerTest(unittest.TestCase):
    def setUp(self):
        self.hashes = {
            L2_PRODUCTION: sha256(L2_PRODUCTION),
            L3_PRODUCTION: sha256(L3_PRODUCTION),
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.l2 = root / "l2.sqlite3"
        self.l3 = root / "l3.sqlite3"
        sqlite_backup(L2_PRODUCTION, self.l2)
        sqlite_backup(L3_PRODUCTION, self.l3)
        result = subprocess.run(
            [
                sys.executable,
                str(MIGRATION_RUNNER),
                "--l3",
                str(self.l3),
                "--migrations-root",
                str(ROOT / "migrations"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with closing(sqlite3.connect(self.l3)) as db:
            db.execute("PRAGMA foreign_keys = ON")
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='derived_features'"
            ).fetchone():
                db.execute("DELETE FROM derived_features")
            db.execute("DELETE FROM quality_assessments")
            db.execute("DELETE FROM source_resolution_decisions")
            db.execute(
                "DELETE FROM processing_checkpoints WHERE pipeline_name='l3b.quality_resolution'"
            )
            db.execute(
                "DELETE FROM definition_registry WHERE definition_id IN (?,?)",
                ("l3b.quality.structural", "l3b.resolution.source"),
            )
            db.commit()

    def tearDown(self):
        self.temp_dir.cleanup()
        for path, expected in self.hashes.items():
            self.assertEqual(sha256(path), expected)

    def run_materializer(self, mode, l3_path=None):
        l3_path = l3_path or self.l3
        self.assertTrue(MATERIALIZER.exists(), f"missing materializer: {MATERIALIZER}")
        result = subprocess.run(
            [
                sys.executable,
                str(MATERIALIZER),
                "--mode",
                mode,
                "--l2",
                str(self.l2),
                "--l3",
                str(l3_path),
                "--quality-definition",
                str(QUALITY_DEFINITION),
                "--resolution-definition",
                str(RESOLUTION_DEFINITION),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def semantic_signatures(database):
        with closing(sqlite3.connect(database)) as db:
            db.row_factory = sqlite3.Row
            quality_inputs = {}
            for row in db.execute(
                """
                SELECT i.assessment_id,i.fact_id,i.input_role
                FROM quality_assessment_inputs i
                JOIN quality_assessments q ON q.id=i.assessment_id
                WHERE q.status='CURRENT'
                """
            ):
                quality_inputs.setdefault(row[0], []).append((row[1], row[2]))
            quality = {
                (
                    row["subject_fact_id"], row["metric"], row["quality_dimension"],
                    row["result"], row["reason_code"], row["details_json"],
                    tuple(sorted(quality_inputs.get(row["id"], []))),
                )
                for row in db.execute(
                    "SELECT * FROM quality_assessments WHERE status='CURRENT'"
                )
            }
            resolution_inputs = {}
            for row in db.execute(
                """
                SELECT i.decision_id,i.fact_id,i.membership_role
                FROM source_resolution_inputs i
                JOIN source_resolution_decisions d ON d.id=i.decision_id
                WHERE d.status='CURRENT'
                """
            ):
                resolution_inputs.setdefault(row[0], []).append((row[1], row[2]))
            resolution = {
                (
                    row["metric"], row["component"], row["grouping_key"],
                    row["decision"], row["outcome"], row["reason_code"],
                    row["details_json"],
                    tuple(sorted(resolution_inputs.get(row["id"], []))),
                )
                for row in db.execute(
                    "SELECT * FROM source_resolution_decisions WHERE status='CURRENT'"
                )
            }
        return quality, resolution

    def test_full_materialization_is_provenance_complete_and_idempotent(self):
        first = self.run_materializer("full")
        second = self.run_materializer("full")
        with closing(sqlite3.connect(self.l3)) as db:
            current_facts = db.execute(
                "SELECT COUNT(*) FROM fact_registry WHERE status='CURRENT'"
            ).fetchone()[0]
            quality = db.execute(
                "SELECT COUNT(*) FROM quality_assessments WHERE status='CURRENT'"
            ).fetchone()[0]
            quality_inputs = db.execute(
                "SELECT COUNT(*) FROM quality_assessment_inputs qai JOIN quality_assessments qa ON qa.id=qai.assessment_id WHERE qa.status='CURRENT'"
            ).fetchone()[0]
            resolution_inputs = db.execute(
                "SELECT COUNT(*) FROM source_resolution_inputs sri JOIN source_resolution_decisions d ON d.id=sri.decision_id WHERE d.status='CURRENT'"
            ).fetchone()[0]
            unresolved = db.execute(
                "SELECT COUNT(*) FROM source_resolution_decisions WHERE status='CURRENT' AND outcome='UNRESOLVED'"
            ).fetchone()[0]
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(quality, current_facts * 3)
        self.assertEqual(quality_inputs, quality)
        self.assertEqual(resolution_inputs, current_facts)
        self.assertGreater(unresolved, 0)
        self.assertGreater(first["quality_inserted"], 0)
        self.assertGreater(first["resolution_inserted"], 0)
        self.assertEqual(second["quality_inserted"], 0)
        self.assertEqual(second["resolution_inserted"], 0)

    def test_input_revision_invalidates_old_outputs_and_recomputes(self):
        self.run_materializer("full")
        with closing(sqlite3.connect(self.l3)) as db:
            db.row_factory = sqlite3.Row
            old = db.execute(
                """
                SELECT fr.*,x.*,fp.l2_logical_record_id,fp.l2_raw_version_id
                FROM fact_registry fr JOIN normalized_point_facts x ON x.fact_id=fr.id
                JOIN fact_provenance fp ON fp.fact_id=fr.id
                WHERE fr.status='CURRENT' AND fr.metric='heart_rate' LIMIT 1
                """
            ).fetchone()
            db.execute(
                "UPDATE fact_registry SET status='STALE' WHERE id=?", (old["id"],)
            )
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
            db.execute(
                """
                INSERT INTO normalized_point_facts VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_id, old["event_time_utc"], old["value_num"] + 1,
                    old["value_code"], old["unit"], old["provider"],
                    old["source_sid"], old["source_class"], old["timezone_name"],
                    old["timezone_offset_seconds"], old["attributes_json"],
                ),
            )
            db.execute(
                """
                INSERT INTO fact_provenance VALUES (?,?,?,'SOURCE',?)
                """,
                (
                    new_id, old["l2_logical_record_id"], old["l2_raw_version_id"],
                    old["created_at_utc"],
                ),
            )
            db.commit()
        self.run_materializer("incremental")
        with closing(sqlite3.connect(self.l3)) as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM quality_assessments WHERE status='CURRENT' AND subject_fact_id=?",
                    (new_id,),
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM quality_assessments WHERE status='CURRENT' AND subject_fact_id=?",
                    (old["id"],),
                ).fetchone()[0],
                0,
            )
            self.assertGreater(
                db.execute(
                    "SELECT COUNT(*) FROM quality_assessments WHERE status='STALE' AND subject_fact_id=?",
                    (old["id"],),
                ).fetchone()[0],
                0,
            )

    def test_reobservation_advances_checkpoint_without_business_changes(self):
        self.run_materializer("full")
        with closing(sqlite3.connect(self.l3)) as db:
            before = (
                db.execute("SELECT COUNT(*) FROM quality_assessments").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM source_resolution_decisions").fetchone()[0],
            )
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
                ) VALUES (?,?,?,?,?,'2027-01-15T10:00:00+00:00','REOBSERVATION',0,?,?,?)
                """,
                (row[0], ordinal, row[3], row[1], row[2], row[4], row[5], row[6]),
            )
            observation_id = cursor.lastrowid
            db.commit()
        result = self.run_materializer("incremental")
        with closing(sqlite3.connect(self.l3)) as db:
            after = (
                db.execute("SELECT COUNT(*) FROM quality_assessments").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM source_resolution_decisions").fetchone()[0],
            )
            checkpoint = db.execute(
                "SELECT last_l2_observation_id FROM processing_checkpoints WHERE pipeline_name='l3b.quality_resolution'"
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(result["quality_inserted"], 0)
        self.assertEqual(result["resolution_inserted"], 0)
        self.assertEqual(checkpoint, observation_id)

    def test_full_and_incremental_current_semantics_are_equivalent(self):
        self.run_materializer("full")
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
        self.run_materializer("incremental", self.l3)
        self.run_materializer("full", comparison)
        self.assertEqual(
            self.semantic_signatures(self.l3),
            self.semantic_signatures(comparison),
        )


if __name__ == "__main__":
    unittest.main()
