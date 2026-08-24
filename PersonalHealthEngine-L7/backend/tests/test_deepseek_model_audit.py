from __future__ import annotations

import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from audit_deepseek_model_config import audit_active_paths, scan_paths  # noqa: E402


def test_static_audit_detects_forbidden_model_and_effort_tokens(tmp_path):
    bad = tmp_path / "runtime.env"
    bad.write_text(
        "DEEPSEEK_MODEL=deepseek-v4-pro\nDEEPSEEK_REASONING_EFFORT=low\n",
        encoding="utf-8",
    )

    findings = scan_paths([bad])

    assert [finding["token"] for finding in findings] == [
        "deepseek-v4-pro",
        "DEEPSEEK_REASONING_EFFORT",
    ]
    assert all(finding["path"].endswith("runtime.env") for finding in findings)


def test_active_production_deepseek_paths_pass_static_audit():
    report = audit_active_paths()

    assert report == {
        "status": "PASS",
        "model": "deepseek-v4-flash",
        "thinking": "disabled",
        "pro_production_references": 0,
        "findings": [],
    }
