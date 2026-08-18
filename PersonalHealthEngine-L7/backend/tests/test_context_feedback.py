"""Phase E tests: Context capture/correction/deletion + Feedback re-evaluation chain."""

import sqlite3

from l7.services.context import ContextService
from l7.services.feedback import FeedbackService

TODAY = "2026-08-17"


def make_context_service(env):
    return ContextService(env["cfg"], env["l7"], env["orch"].bridge, env["orch"],
                          reasoning_adapter=env["adapter"])


def make_feedback_service(env):
    return FeedbackService(env["cfg"], env["l7"], env["orch"].bridge, env["orch"],
                           reasoning_adapter=env["adapter"])


def l6_rows(env, table, where="1=1", params=()):
    con = sqlite3.connect(env["l6_copy"])
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"SELECT * FROM {table} WHERE {where}", params)]
    con.close()
    return rows


# ---------------------------------------------------------------- Context

def test_context_ingest_structures_and_stores_user_fact(env):
    svc = make_context_service(env)
    r = svc.ingest("owner", "昨晚熬夜到三点才睡", today=TODAY)
    assert r["status"] == "SAVED"
    types = [e["context_type"] for e in r["events"]]
    assert "LATE_SLEEP" in types

    rows = l6_rows(env, "personal_context",
                   "status='CURRENT' AND context_type='LATE_SLEEP' AND raw_text=?",
                   ("昨晚熬夜到三点才睡",))
    assert len(rows) == 1  # exactly the user's own statement, nothing duplicated
    new = rows[0]
    assert new["source"] == "USER_REPORTED"
    assert new["raw_text"] == "昨晚熬夜到三点才睡"

    # Time semantics recorded (§28): one-shot event, expires after its day.
    meta = env["l7"].execute(
        "SELECT * FROM context_time_meta WHERE l6_context_id=?", (new["id"],)
    ).fetchone()
    assert meta is not None
    assert meta["ongoing"] == 0
    assert meta["valid_until"] is not None

    # Midday Context update E2E: fact base changed -> engine re-analyzed Today.
    assert r["re_evaluation"]["outcome"] == "REMATERIALIZED"
    assert r["re_evaluation"]["model_calls"] == 1


def test_context_ingest_empty_text_rejected(env):
    svc = make_context_service(env)
    try:
        svc.ingest("owner", "   ")
        assert False, "should raise"
    except ValueError:
        pass


def test_context_unstructured_text_does_not_fabricate(env):
    svc = make_context_service(env)
    r = svc.ingest("owner", "今天天气真不错啊", today=TODAY)
    assert r["status"] == "NO_STRUCTURED_FACT"
    assert r["context_ids"] == []
    # Nothing invented in L6 from small talk.
    assert l6_rows(env, "personal_context", "raw_text=?", ("今天天气真不错啊",)) == []


def test_context_correction_supersedes_and_updates_today(env):
    svc = make_context_service(env)
    r = svc.ingest("owner", "昨晚熬夜了", today=TODAY)
    target_id = r["context_ids"][0]

    # E2E Correction: user says the real situation is a fever — old fact must lose authority.
    c = svc.correct("owner", target_id, "其实我昨晚开始发烧了", today=TODAY)
    assert c["status"] == "CORRECTED"

    old = l6_rows(env, "personal_context", "id=?", (target_id,))[0]
    assert old["status"] == "SUPERSEDED"

    new_rows = l6_rows(env, "personal_context",
                       "status='CURRENT' AND supersedes_id=?", (target_id,))
    assert new_rows and new_rows[0]["context_type"] == "FEVER"
    assert new_rows[0]["source"] == "USER_REPORTED"

    revisions = l6_rows(env, "context_revisions", "context_id=? AND revision_kind='CORRECTION'",
                        (target_id,))
    assert len(revisions) == 1 and revisions[0]["prior_json"]

    # Today must reflect the safety-relevant correction (state E).
    today = env["service"].get_today("owner", trigger="test")
    assert today["product_state"] == "E"
    assert today["information_order"] == ["conclusion", "action", "cause"]


def test_context_deletion_keeps_provenance(env):
    svc = make_context_service(env)
    r = svc.ingest("owner", "昨天喝了酒", today=TODAY)
    target_id = r["context_ids"][0]
    d = svc.delete("owner", target_id)
    assert d["status"] == "DELETED"
    old = l6_rows(env, "personal_context", "id=?", (target_id,))[0]
    assert old["status"] == "SUPERSEDED"
    revisions = l6_rows(env, "context_revisions", "context_id=? AND revision_kind='DELETION'",
                        (target_id,))
    assert len(revisions) == 1


def test_expire_sweep_ends_stale_ongoing_contexts(env):
    svc = make_context_service(env)
    r = svc.ingest("owner", "我感冒了，嗓子疼", today=TODAY)
    assert r["events"], "symptom text must extract events"
    ctx_id = r["context_ids"][0]
    meta = env["l7"].execute(
        "SELECT ongoing FROM context_time_meta WHERE l6_context_id=?", (ctx_id,)
    ).fetchone()
    assert meta["ongoing"] == 1, "symptoms are ongoing states"

    # Far future: the validity window has passed -> sweep marks it ended.
    affected = svc.expire_sweep("owner", "2026-12-31")
    assert ctx_id in affected
    meta2 = env["l7"].execute(
        "SELECT ended_on FROM context_time_meta WHERE l6_context_id=?", (ctx_id,)
    ).fetchone()
    assert meta2["ended_on"] == "2026-12-31"


# ---------------------------------------------------------------- Feedback

def test_feedback_confirmed_advances_patterns(env):
    env["orch"].evaluate("owner", "app_open")  # ensure a CURRENT reasoning exists
    fb = make_feedback_service(env)
    r = fb.submit("owner", "准确")
    assert r["feedback_status"] == "CONFIRMED"
    assert r["category"] == "judgment_confirmed"

    frows = l6_rows(env, "user_feedback")
    assert any(f["feedback_status"] == "CONFIRMED" and f["source"] == "USER_FEEDBACK"
               for f in frows)

    # Pattern learning: the CURRENT bundle has recent context + deviations, so at least one
    # trigger::signal counter advanced its total.
    prows = l6_rows(env, "personal_patterns")
    assert max(p["total_count"] for p in prows) >= 2


def test_feedback_rejected_does_not_advance_support(env):
    env["orch"].evaluate("owner", "app_open")
    fb = make_feedback_service(env)
    before = {p["pattern_key"]: p["support_count"] for p in l6_rows(env, "personal_patterns")}
    r = fb.submit("owner", "不太准确")
    assert r["feedback_status"] == "REJECTED"
    after = {p["pattern_key"]: p["support_count"] for p in l6_rows(env, "personal_patterns")}
    assert before == after, "rejection must not add support"


def test_feedback_correction_ingests_context_and_updates_today(env):
    env["orch"].evaluate("owner", "app_open")
    fb = make_feedback_service(env)
    r = fb.submit("owner", "补充情况", text="其实是我昨天喝酒了")
    assert r["category"] == "context_added"
    assert r["feedback_status"] == "CORRECTED"
    assert r["corrected_context_ids"], "correction text with a real fact becomes context"

    ctx = l6_rows(env, "personal_context", "context_type='ALCOHOL_USE' AND status='CURRENT'")
    assert ctx and ctx[0]["source"] == "USER_REPORTED"
    assert r["re_evaluation"]["outcome"] in ("REMATERIALIZED", "BUNDLE_UNCHANGED")


def test_feedback_supplement_without_fact_is_reason_corrected(env):
    env["orch"].evaluate("owner", "app_open")
    fb = make_feedback_service(env)
    r = fb.submit("owner", "补充情况", text="我觉得原因分析不太对")
    assert r["category"] == "reason_corrected"
    assert r["feedback_status"] == "CORRECTED"
    assert r["corrected_context_ids"] == []


def test_feedback_unknown_verdict_rejected(env):
    fb = make_feedback_service(env)
    try:
        fb.submit("owner", "非常满意")
        assert False, "should raise"
    except ValueError:
        pass
