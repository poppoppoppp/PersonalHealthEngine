from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from validate_deepseek_flash_paths import (  # noqa: E402
    classify_invocations,
    render_acceptance,
    temporary_l6_copy,
)


def invocation(operation: str, model: str = "deepseek-v4-flash") -> dict:
    return {
        "event": "deepseek_invocation",
        "operation": operation,
        "requested_model": model,
        "response_model": model,
        "thinking": "disabled",
        "usage": {"total_tokens": 10},
    }


def test_classifies_required_real_flash_paths_and_feedback_contract():
    report = classify_invocations(
        [invocation("today"), invocation("qna"), invocation("context")],
        persisted_models=["deepseek-v4-flash", "deepseek-v4-flash"],
    )

    assert report["model_audit"] == "PASS"
    assert report["v4_flash"] == "ACTIVE"
    assert report["v4_pro_production_calls"] == 0
    assert report["thinking_mode"] == "DISABLED"
    assert report["today_real_flash_call"] == "PASS"
    assert report["qna_real_flash_call"] == "PASS"
    assert report["context_real_flash_call"] == "PASS"
    assert report["feedback_real_flash_call"] == "NOT_APPLICABLE"
    assert report["medgemma"] == "UNCHANGED"


def test_classification_rejects_any_pro_or_non_disabled_record():
    report = classify_invocations([
        invocation("today"),
        invocation("qna", model="deepseek-v4-pro"),
        {**invocation("context"), "thinking": "enabled"},
    ], persisted_models=["deepseek-v4-pro"])

    assert report["model_audit"] == "FAIL"
    assert report["v4_pro_production_calls"] == 2
    assert report["qna_real_flash_call"] == "FAIL"
    assert report["context_real_flash_call"] == "FAIL"


def test_acceptance_output_is_secret_and_content_free():
    records = [
        {
            **invocation("today"),
            "api_key": "should-never-render",
            "prompt": "private health prompt",
            "content": "private model answer",
        },
        invocation("qna"),
        invocation("context"),
    ]
    rendered = render_acceptance(classify_invocations(
        records,
        persisted_models=["deepseek-v4-flash"],
    ))

    assert "should-never-render" not in rendered
    assert "private health prompt" not in rendered
    assert "private model answer" not in rendered
    assert "TODAY REAL FLASH CALL = PASS" in rendered
    assert "FEEDBACK REAL FLASH CALL = NOT_APPLICABLE" in rendered


def test_temporary_l6_copy_is_removed_after_validation(tmp_path):
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE marker (value TEXT)")
    connection.execute("INSERT INTO marker VALUES ('preserved')")
    connection.commit()
    connection.close()

    with temporary_l6_copy(source) as copied:
        copied_path = Path(copied)
        temp_parent = copied_path.parent
        assert copied_path != source
        assert sqlite3.connect(copied_path).execute("SELECT value FROM marker").fetchone()[0] == "preserved"

    assert source.exists()
    assert not temp_parent.exists()
