import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
AUDIT = ROOT / "scripts" / "l3b_acceptance_v0_1.py"


class L3BAcceptanceTest(unittest.TestCase):
    def test_production_l3b_acceptance_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "l3b.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT),
                    "--l2",
                    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3",
                    "--l3",
                    str(ROOT / "db" / "personal_health_features.sqlite3"),
                    "--quality-definition",
                    str(ROOT / "definitions" / "quality" / "l3b_structural_quality_v0_1.json"),
                    "--resolution-definition",
                    str(ROOT / "definitions" / "resolution" / "l3b_source_resolution_v0_1.json"),
                    "--output",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["checks_failed"], 0)
        self.assertGreaterEqual(report["checks_passed"], 18)


if __name__ == "__main__":
    unittest.main()
