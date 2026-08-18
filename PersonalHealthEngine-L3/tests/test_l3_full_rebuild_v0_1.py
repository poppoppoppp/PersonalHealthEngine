import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
RUNNER = ROOT / "scripts" / "l3_full_rebuild_v0_1.py"


class Layer3FullRebuildTest(unittest.TestCase):
    def test_empty_l3_rebuild_is_semantically_equivalent_to_production(self):
        self.assertTrue(RUNNER.exists(), f"missing full rebuild runner: {RUNNER}")
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--l2",
                    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3",
                    "--production-l3",
                    str(ROOT / "db" / "personal_health_features.sqlite3"),
                    "--output-dir",
                    temp_dir,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(
                (Path(temp_dir) / "FULL_REBUILD_ACCEPTANCE.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["semantic_equivalence"], "PASS")
        self.assertEqual(report["checks_failed"], 0)


if __name__ == "__main__":
    unittest.main()
