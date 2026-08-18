import hashlib
import json
import shutil
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
SCRIPT = ROOT / "scripts" / "normalize_sleep_v0_1.py"
DEFINITION = ROOT / "definitions" / "normalizers" / "sleep_v0_1.json"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_backup(source, destination):
    source_uri = "file:" + source.as_posix() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_db:
        with closing(sqlite3.connect(destination)) as destination_db:
            source_db.backup(destination_db)


class SleepZeroDurationAcceptance(unittest.TestCase):
    def setUp(self):
        self.production_hashes = {
            PRODUCTION_L2: sha256(PRODUCTION_L2),
            PRODUCTION_L3: sha256(PRODUCTION_L3),
        }
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sandbox = Path(self.temp_dir.name)
        (self.sandbox / "db").mkdir()
        (self.sandbox / "definitions" / "normalizers").mkdir(parents=True)
        sqlite_backup(
            PRODUCTION_L2,
            self.sandbox / "personal_health_raw.sqlite3",
        )
        sqlite_backup(
            PRODUCTION_L3,
            self.sandbox / "db" / "personal_health_features.sqlite3",
        )
        shutil.copy2(
            DEFINITION,
            self.sandbox / "definitions" / "normalizers" / DEFINITION.name,
        )
        source = SCRIPT.read_text(encoding="utf-8")
        source = source.replace(
            'ROOT = Path(r"D:\\PersonalHealthEngine-L3")',
            f"ROOT = Path({str(self.sandbox)!r})",
        )
        source = source.replace(
            'r"D:\\PersonalHealthEngine-L2\\db\\personal_health_raw.sqlite3"',
            repr(str(self.sandbox / "personal_health_raw.sqlite3")),
        )
        self.sandbox_script = self.sandbox / SCRIPT.name
        self.sandbox_script.write_text(source, encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()
        for path, expected in self.production_hashes.items():
            self.assertEqual(sha256(path), expected)

    def run_normalizer(self):
        return subprocess.run(
            [sys.executable, str(self.sandbox_script)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_zero_duration_vendor_segments_are_preserved(self):
        result = self.run_normalizer()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        l3_path = self.sandbox / "db" / "personal_health_features.sqlite3"
        with closing(sqlite3.connect(l3_path)) as db:
            total, zero_duration = db.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN inf.duration_seconds = 0 THEN 1 ELSE 0 END)
                FROM fact_registry fr
                JOIN normalized_interval_facts inf ON inf.fact_id = fr.id
                WHERE fr.status = 'CURRENT'
                  AND fr.definition_id = 'normalize.sleep'
                """
            ).fetchone()
        self.assertEqual(total, 163)
        self.assertEqual(zero_duration, 2)

    def test_negative_duration_vendor_segment_is_rejected(self):
        l2_path = self.sandbox / "personal_health_raw.sqlite3"
        with closing(sqlite3.connect(l2_path)) as db:
            raw_version_id, raw_json = db.execute(
                """
                SELECT id, raw_json
                FROM raw_record_versions
                WHERE logical_record_id = 5030
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            outer = json.loads(raw_json)
            inner = json.loads(outer["value"])
            inner["items"][5]["end_time"] = inner["items"][5]["start_time"] - 1
            outer["value"] = json.dumps(inner, separators=(",", ":"))
            db.execute(
                "UPDATE raw_record_versions SET raw_json = ? WHERE id = ?",
                (json.dumps(outer, separators=(",", ":")), raw_version_id),
            )
            db.commit()

        result = self.run_normalizer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("logical 5030: item 5 invalid interval", result.stderr)


if __name__ == "__main__":
    unittest.main()
