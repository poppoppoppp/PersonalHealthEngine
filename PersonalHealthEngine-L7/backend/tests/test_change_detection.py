"""Integration tests: L6 -> L7 change detection, model-call discipline, semantic stability.

These run against a per-test copy of the real sealed L6 production database (plus read-only
production L3/L4/L5), so assertions reflect the actual engine state, not synthetic fixtures.
"""

import json
import sqlite3
from pathlib import Path


DEGRADED_REASONING = "（推理模型暂不可用，仅保留结构化证据。）"


def set_degraded_reasoning(l6_write):
    l6_write.execute(
        "UPDATE daily_reasoning SET reasoning_summary=?,recommended_actions_json='[]' "
        "WHERE status='CURRENT'",
        (DEGRADED_REASONING,),
    )
    l6_write.commit()


def read_l6_current(l6_path, analysis_date):
    con = sqlite3.connect(f"file:{l6_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        dr = con.execute(
            "SELECT * FROM daily_reasoning WHERE analysis_date=? AND status='CURRENT'",
            (analysis_date,),
        ).fetchall()
        bundles = con.execute(
            "SELECT * FROM evidence_bundles WHERE analysis_date=? AND status='CURRENT'",
            (analysis_date,),
        ).fetchall()
        return [dict(r) for r in dr], [dict(r) for r in bundles]
    finally:
        con.close()


def test_first_evaluation_uses_existing_reasoning_without_any_model_call(env):
    """The sealed L6 db already contains a CURRENT reasoning for 2026-08-16 built from the
    same deterministic bundle. L7 must recognize the unchanged bundle hash and perform zero
    model calls (cost rule: never call DeepSeek when nothing changed)."""
    result = env["orch"].evaluate("owner", "app_open")
    assert result.outcome == "BUNDLE_UNCHANGED"
    assert result.model_calls == 0
    assert env["adapter"].reason_daily_calls == 0

    p = result.today_payload
    assert p["product_state"] == "C"
    assert p["product_state_label"] == "今天值得调整"
    assert p["cause"]["hypothesis_type"] == "SLEEP_DEFICIT"
    assert len(p["actions"]) == 3
    assert p["analysis_date"] == "2026-08-16"
    assert p["confidence"] == "LOW"
    assert p["judgment_updated"] is False
    assert p["evidence_level2"], "Evidence Level 2 must not be empty for a notable change"


def test_second_evaluation_is_a_no_op(env):
    r1 = env["orch"].evaluate("owner", "app_open")
    r2 = env["orch"].evaluate("owner", "app_open")
    assert r2.outcome == "NO_UPSTREAM_CHANGE"
    assert r2.model_calls == 0
    assert env["adapter"].reason_daily_calls == 0
    assert r1.today_payload["version_id"] == r2.today_payload["version_id"]
    assert r2.today_payload["judgment_updated"] is False


def test_manual_refresh_recovers_degraded_reasoning_without_changing_judgment(env, l6_write):
    set_degraded_reasoning(l6_write)

    degraded = env["orch"].evaluate("owner", "app_open")
    assert DEGRADED_REASONING in degraded.today_payload["cause"]["text"]
    assert env["adapter"].reason_daily_calls == 0

    recovered = env["orch"].evaluate("owner", "manual_refresh")

    assert env["adapter"].reason_daily_calls == 1
    assert DEGRADED_REASONING not in recovered.today_payload["cause"]["text"]
    assert recovered.today_payload["judgment_updated"] is False
    assert recovered.today_payload["version_id"] != degraded.today_payload["version_id"]
    versions = env["l7"].execute(
        "SELECT l6_daily_reasoning_id FROM today_versions WHERE user_id='owner' ORDER BY id"
    ).fetchall()
    assert len(versions) == 2
    assert versions[0]["l6_daily_reasoning_id"] == versions[1]["l6_daily_reasoning_id"]


def test_failed_degraded_reasoning_recovery_keeps_current_projection(env, l6_write):
    set_degraded_reasoning(l6_write)
    degraded = env["orch"].evaluate("owner", "app_open")
    attempts = 0

    def fail_recovery(bundle, candidates):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("temporary model outage")

    env["adapter"].reason_daily = fail_recovery
    result = env["orch"].evaluate("owner", "manual_refresh")

    assert attempts == 3, "nondeterministic real models justify bounded retries"
    assert result.model_calls == 3
    assert result.today_payload["version_id"] == degraded.today_payload["version_id"]
    assert DEGRADED_REASONING in result.today_payload["cause"]["text"]
    count = env["l7"].execute(
        "SELECT COUNT(*) AS n FROM today_versions WHERE user_id='owner'"
    ).fetchone()["n"]
    assert count == 1


def test_invalid_cached_recovery_output_is_retried(env, l6_write, monkeypatch):
    from l7.engine import model_cache

    set_degraded_reasoning(l6_write)
    env["orch"].evaluate("owner", "app_open")
    monkeypatch.setattr(
        model_cache,
        "lookup",
        lambda *_args: {
            "primary_hypothesis_type": "NOT_ALLOWED",
            "secondary_hypothesis_type": None,
            "confidence": "LOW",
            "reasoning_summary": "无效缓存",
            "recommended_actions": [],
        },
    )

    result = env["orch"].evaluate("owner", "manual_refresh")

    assert env["adapter"].reason_daily_calls == 1
    assert DEGRADED_REASONING not in result.today_payload["cause"]["text"]


def test_hypothesis_changing_recovery_is_rejected(env, l6_write):
    set_degraded_reasoning(l6_write)
    degraded = env["orch"].evaluate("owner", "app_open")
    original_reason_daily = env["adapter"].reason_daily

    def change_hypothesis(bundle, candidates):
        output = original_reason_daily(bundle, candidates)
        output["primary_hypothesis_type"] = "UNKNOWN"
        return output

    env["adapter"].reason_daily = change_hypothesis
    result = env["orch"].evaluate("owner", "manual_refresh")

    assert env["adapter"].reason_daily_calls == 3
    assert result.today_payload["version_id"] == degraded.today_payload["version_id"]
    assert DEGRADED_REASONING in result.today_payload["cause"]["text"]


def test_model_added_secondary_does_not_block_recovery(env, l6_write):
    """Production deadlock observed 2026-08-29: the model returns the matching primary
    plus an extra secondary that the deterministic candidate list never had. Display
    recovery keeps the sealed judgment fields, so the extra secondary must not reject
    the wording."""
    set_degraded_reasoning(l6_write)
    degraded = env["orch"].evaluate("owner", "app_open")
    original_reason_daily = env["adapter"].reason_daily

    def add_secondary(bundle, candidates):
        output = original_reason_daily(bundle, candidates)
        output["secondary_hypothesis_type"] = "RECOVERY_STRAIN"
        return output

    env["adapter"].reason_daily = add_secondary
    result = env["orch"].evaluate("owner", "manual_refresh")

    assert env["adapter"].reason_daily_calls == 1
    assert DEGRADED_REASONING not in result.today_payload["cause"]["text"]
    # The sealed judgment is untouched: secondary stays None in the projection.
    assert result.today_payload["cause"]["secondary"] is None
    assert result.today_payload["cause"]["hypothesis_type"] == "SLEEP_DEFICIT"
    assert result.today_payload["judgment_updated"] is False


def test_irrelevant_context_change_does_not_rewrite_wording(env, l6_write):
    """Adding a context that does not change the judgment must re-materialize (evidence
    changed) but keep the exact presented wording (semantic stability, §10)."""
    r1 = env["orch"].evaluate("owner", "app_open")
    l6_write.execute(
        "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
        "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
        ("2026-08-16", "CAFFEINE", None, None, "下午喝了咖啡", "USER_REPORTED",
         "2026-08-17T12:00:00+00:00", "2026-08-17T12:00:00+00:00"),
    )
    l6_write.commit()

    r2 = env["orch"].evaluate("owner", "context_added")
    assert r2.outcome == "REMATERIALIZED"
    assert r2.model_calls == 1, "bundle changed -> exactly one reasoning call"

    drs, bundles = read_l6_current(env["l6_copy"], "2026-08-16")
    assert len(drs) == 1 and len(bundles) == 1, "append-only versioning keeps one CURRENT row"

    # Judgment unchanged -> same version row, same wording, no '判断已更新'.
    assert r2.today_payload["version_id"] == r1.today_payload["version_id"]
    assert r2.today_payload["headline"] == r1.today_payload["headline"]
    assert r2.today_payload["actions"] == r1.today_payload["actions"]
    assert r2.today_payload["cause"] == r1.today_payload["cause"]
    assert r2.today_payload["judgment_updated"] is False


def test_rematerialization_records_current_l6_pipeline_metadata(env, l6_write):
    l6_write.execute(
        "UPDATE processing_checkpoints SET last_l5_analytic_id=0,last_l3_feature_id=0,"
        "last_l4_baseline_id=0 WHERE pipeline_name='l6.daily_reasoning'"
    )
    l6_write.execute(
        "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
        "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
        ("2026-08-16", "STRESS", None, None, "最近工作压力大", "USER_REPORTED",
         "2026-08-17T12:30:00+00:00", "2026-08-17T12:30:00+00:00"),
    )
    l6_write.commit()

    result = env["orch"].evaluate("owner", "context_added")
    assert result.outcome == "REMATERIALIZED"

    upstreams = []
    for path, query in (
        (env["cfg"].l5_db, "SELECT COALESCE(MAX(id),0) FROM deviation_analytics"),
        (env["cfg"].l3_db, "SELECT COALESCE(MAX(id),0) FROM derived_features"),
        (env["cfg"].l4_db, "SELECT COALESCE(MAX(id),0) FROM rolling_baselines"),
    ):
        con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            upstreams.append(con.execute(query).fetchone()[0])
        finally:
            con.close()

    con = sqlite3.connect(env["l6_copy"])
    try:
        checkpoint = con.execute(
            "SELECT last_l5_analytic_id,last_l3_feature_id,last_l4_baseline_id,"
            "last_successful_run_id FROM processing_checkpoints "
            "WHERE pipeline_name='l6.daily_reasoning'"
        ).fetchone()
        latest_run = con.execute(
            "SELECT run_id,mode,status FROM pipeline_runs "
            "ORDER BY started_at_utc DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()

    assert checkpoint[:3] == tuple(upstreams)
    assert checkpoint[3] == latest_run[0]
    assert latest_run[1:] == ("INCREMENTAL", "PASS")


def test_symptom_context_changes_judgment_and_marks_update(env, l6_write):
    """A safety-relevant fact must flip the product state to E, reorder information
    (conclusion -> action -> cause), and set judgment_updated."""
    r1 = env["orch"].evaluate("owner", "app_open")
    l6_write.execute(
        "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
        "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
        ("2026-08-16", "FEVER", None, None, "我发烧了", "USER_REPORTED",
         "2026-08-17T13:00:00+00:00", "2026-08-17T13:00:00+00:00"),
    )
    l6_write.commit()

    r2 = env["orch"].evaluate("owner", "context_added")
    assert r2.outcome == "REMATERIALIZED"
    p = r2.today_payload
    assert p["product_state"] == "E"
    assert p["medical_attention"] is True
    assert p["information_order"] == ["conclusion", "action", "cause"]
    assert p["judgment_updated"] is True
    assert p["version_id"] != r1.today_payload["version_id"]

    con = sqlite3.connect(env["l6_copy"])
    try:
        medical_invocation = con.execute(
            "SELECT status FROM model_invocations WHERE adapter_kind='MEDICAL' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    assert medical_invocation == ("PASS",)

    # Old version preserved (never overwritten, §17).
    versions = env["l7"].execute(
        "SELECT product_state FROM today_versions WHERE user_id='owner' ORDER BY id"
    ).fetchall()
    assert [v["product_state"] for v in versions] == ["C", "E"]


def test_model_call_cache_prevents_repeat_payment(env, l6_write):
    """Force the same bundle change twice from scratch: the second reasoning request must be
    served from the provenance-aware cache (0 new adapter calls)."""
    env["orch"].evaluate("owner", "app_open")
    l6_write.execute(
        "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
        "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
        ("2026-08-16", "STRESS", None, None, "最近工作压力大", "USER_REPORTED",
         "2026-08-17T14:00:00+00:00", "2026-08-17T14:00:00+00:00"),
    )
    l6_write.commit()
    r2 = env["orch"].evaluate("owner", "context_added")
    assert r2.model_calls == 1
    assert env["adapter"].reason_daily_calls == 1

    cached = env["l7"].execute("SELECT COUNT(*) AS n FROM model_call_cache").fetchone()["n"]
    assert cached >= 1

    # Revert context (supersede). The original bundle was materialized by the sealed L6 run,
    # never paid for by L7, so restoring it costs exactly one (first-time) call.
    l6_write.execute(
        "UPDATE personal_context SET status='SUPERSEDED' WHERE context_type='STRESS'"
    )
    l6_write.commit()
    r3 = env["orch"].evaluate("owner", "context_added")
    assert r3.model_calls == 1
    calls_now = env["adapter"].reason_daily_calls
    assert calls_now == 2

    # Re-add the same STRESS context: its request hash is already in the provenance-aware
    # cache, so L7 must materialize again with ZERO new adapter calls.
    l6_write.execute(
        "INSERT INTO personal_context (context_date,context_type,body_part,severity,raw_text,"
        "source,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?,?,?,'CURRENT',?,?)",
        ("2026-08-16", "STRESS", None, None, "最近工作压力大", "USER_REPORTED",
         "2026-08-17T15:00:00+00:00", "2026-08-17T15:00:00+00:00"),
    )
    l6_write.commit()
    r4 = env["orch"].evaluate("owner", "context_added")
    assert r4.outcome == "REMATERIALIZED"
    assert r4.model_calls == 0, "identical request hash must be served from the model-call cache"
    assert env["adapter"].reason_daily_calls == calls_now


def test_no_data_fallback_is_state_D(env, tmp_path):
    """An empty L5 (no deviation analytics) must yield '目前无法可靠判断', never a guess."""
    import sqlite3 as s
    empty_l5 = tmp_path / "empty_l5.sqlite3"
    con = s.connect(empty_l5)
    con.execute("CREATE TABLE deviation_analytics (id INTEGER PRIMARY KEY, feature_date TEXT, status TEXT)")
    con.commit()
    con.close()
    env["cfg"].l5_db = str(empty_l5)
    result = env["orch"].evaluate("owner", "app_open")
    assert result.outcome == "FALLBACK_NO_DATA"
    assert result.today_payload["product_state"] == "D"
    assert result.today_payload["actions"] == []
    assert env["adapter"].reason_daily_calls == 0


def test_scheduled_model_gate_matrix():
    from datetime import datetime

    from l7.engine.orchestrator import scheduled_model_worthy

    noon = datetime(2026, 8, 29, 14, 0)
    morning_window = datetime(2026, 8, 29, 8, 30)
    evening_window = datetime(2026, 8, 29, 19, 30)
    outside_before = datetime(2026, 8, 29, 7, 30)
    outside_after = datetime(2026, 8, 29, 20, 30)

    # First analysis of the day: always worth the model, any time.
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", noon, False, False) is True
    # User added/corrected context or feedback: worth it, any time.
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", noon, True, True) is True
    # The judged day is complete: worth it.
    assert scheduled_model_worthy("2026-08-28", "2026-08-29", noon, True, False) is True
    # Inside the two fixed windows: worth it.
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", morning_window, True, False) is True
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", evening_window, True, False) is True
    # Outside windows, partial day, no user input: skip the model.
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", noon, True, False) is False
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", outside_before, True, False) is False
    assert scheduled_model_worthy("2026-08-29", "2026-08-29", outside_after, True, False) is False


def test_reference_ranges_and_safety_floors():
    from l7.rendering.reference_ranges import reference_for, safety_breach

    # 展示参考带：有临床标准的指标才有
    assert reference_for("resting_heart_rate") == {"low": 60, "high": 100}
    assert reference_for("spo2") == {"low": 95, "high": 100}
    assert reference_for("steps") is None, "生活方式指标没有临床标准，不给参考带"

    # 安全底座：与个人基线无关的硬阈值
    assert safety_breach("spo2", 97.8) is None
    assert safety_breach("spo2", 88.0) == "low"
    assert safety_breach("resting_heart_rate", 64.0) is None
    assert safety_breach("resting_heart_rate", 130.0) == "high"
    assert safety_breach("resting_heart_rate", 35.0) == "low"
    assert safety_breach("heart_rate", 150.0) == "high"
    assert safety_breach("steps", 100000) is None
