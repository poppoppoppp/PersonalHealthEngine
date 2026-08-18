import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
MIGRATION = ROOT / "migrations" / "008_l3c_derived_features.sql"


class L3CSchemaTest(unittest.TestCase):
    def test_migration_creates_feature_and_three_provenance_relations(self):
        self.assertTrue(MIGRATION.exists(), f"missing migration: {MIGRATION}")
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "l3.sqlite3"
            with closing(sqlite3.connect(database)) as db:
                db.execute("PRAGMA foreign_keys = ON")
                for version in range(1, 9):
                    matches = list((ROOT / "migrations").glob(f"{version:03d}_*.sql"))
                    self.assertEqual(len(matches), 1)
                    db.executescript(matches[0].read_text(encoding="utf-8-sig"))
                db.execute("PRAGMA user_version = 8")
                tables = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertTrue(
                    {
                        "derived_features",
                        "derived_feature_fact_inputs",
                        "derived_feature_quality_inputs",
                        "derived_feature_resolution_inputs",
                    }
                    <= tables
                )
                self.assertEqual(
                    len(db.execute("PRAGMA foreign_key_list(derived_feature_fact_inputs)").fetchall()),
                    2,
                )
                self.assertEqual(
                    len(db.execute("PRAGMA foreign_key_list(derived_feature_quality_inputs)").fetchall()),
                    2,
                )
                self.assertEqual(
                    len(db.execute("PRAGMA foreign_key_list(derived_feature_resolution_inputs)").fetchall()),
                    2,
                )


if __name__ == "__main__":
    unittest.main()
