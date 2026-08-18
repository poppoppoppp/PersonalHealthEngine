import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r"D:\PersonalHealthEngine-L3")
AUDIT = ROOT / "scripts" / "l3_final_audit_v0_1.py"


class Layer3FinalAuditTest(unittest.TestCase):
    def test_final_audit_has_no_failed_core_checks(self):
        self.assertTrue(AUDIT.exists(), f"missing final audit: {AUDIT}")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "audit.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT),
                    "--root",
                    str(ROOT),
                    "--l2",
                    r"D:\PersonalHealthEngine-L2\db\personal_health_raw.sqlite3",
                    "--l3",
                    str(ROOT / "db" / "personal_health_features.sqlite3"),
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
        self.assertGreaterEqual(report["checks_total"], 49)


if __name__ == "__main__":
    unittest.main()
