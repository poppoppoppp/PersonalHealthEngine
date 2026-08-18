import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
MIGRATION = ROOT / "migrations" / "007_l3b_quality_resolution.sql"
MIGRATION_RUNNER = ROOT / "scripts" / "apply_migrations_v0_1.py"
PRODUCTION_L3 = ROOT / "db" / "personal_health_features.sqlite3"


def sqlite_backup(source, destination):
    uri = "file:" + source.as_posix() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)


class L3BSchemaTest(unittest.TestCase):
    def test_migration_creates_versioned_provenance_aware_l3b_schema(self):
        self.assertTrue(MIGRATION.exists(), f"missing migration: {MIGRATION}")
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "l3.sqlite3"
            with closing(sqlite3.connect(database)) as db:
                db.execute("PRAGMA foreign_keys = ON")
                db.executescript(
                    (ROOT / "migrations" / "001_foundation.sql").read_text(
                        encoding="utf-8-sig"
                    )
                )
                db.executescript(
                    (ROOT / "migrations" / "002_point_fact_core.sql").read_text(
                        encoding="utf-8-sig"
                    )
                )
                db.executescript(MIGRATION.read_text(encoding="utf-8"))
                db.execute("PRAGMA user_version = 7")
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(
                    {
                        "quality_assessments",
                        "quality_assessment_inputs",
                        "source_resolution_decisions",
                        "source_resolution_inputs",
                    }
                    <= tables
                )
                self.assertEqual(
                    len(db.execute("PRAGMA foreign_key_list(quality_assessments)").fetchall()),
                    1,
                )
                self.assertEqual(
                    len(db.execute("PRAGMA foreign_key_list(quality_assessment_inputs)").fetchall()),
                    2,
                )
                self.assertEqual(
                    len(db.execute("PRAGMA foreign_key_list(source_resolution_inputs)").fetchall()),
                    2,
                )
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 7)

    def test_migration_runner_is_atomic_and_idempotent(self):
        self.assertTrue(MIGRATION_RUNNER.exists(), f"missing runner: {MIGRATION_RUNNER}")
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "l3.sqlite3"
            sqlite_backup(PRODUCTION_L3, database)
            command = [
                sys.executable,
                str(MIGRATION_RUNNER),
                "--l3",
                str(database),
                "--migrations-root",
                str(ROOT / "migrations"),
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM schema_migrations WHERE version=7"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())

    def test_migration_runner_builds_an_empty_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "empty.sqlite3"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MIGRATION_RUNNER),
                    "--l3",
                    str(database),
                    "--migrations-root",
                    str(ROOT / "migrations"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with closing(sqlite3.connect(database)) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 8)
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
                    8,
                )


if __name__ == "__main__":
    unittest.main()
