"""Integration tests: L6 -> L7 change detection, model-call discipline, semantic stability.

These run against a per-test copy of the real sealed L6 production database (plus read-only
production L3/L4/L5), so assertions reflect the actual engine state, not synthetic fixtures.
"""

import json
import sqlite3


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
