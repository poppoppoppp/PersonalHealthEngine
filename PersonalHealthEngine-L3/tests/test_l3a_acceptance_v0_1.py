import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
AUDIT = ROOT / "scripts" / "l3a_acceptance_v0_1.py"
L2 = Path(r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3")
L3 = ROOT / "db" / "personal_health_features.sqlite3"


class L3AAcceptanceTest(unittest.TestCase):
    def test_production_l3a_acceptance_is_complete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "l3a.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT),
                    "--l2",
                    str(L2),
                    "--l3",
                    str(L3),
                    "--definitions-root",
                    str(ROOT / "definitions" / "normalizers"),
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
        self.assertGreaterEqual(report["checks_passed"], 24)


if __name__ == "__main__":
    unittest.main()
