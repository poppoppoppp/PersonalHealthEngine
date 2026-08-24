"""Phase E tests: Q&A grounding, medical routing, conversation rollover, scope guard."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from l7.services.qna import QnAService, in_health_scope
from conftest import PROD_L6  # noqa: F401  (ensures path setup order is stable)


def make_qna(env):
    return QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=env["adapter"],
        medical_adapter=None,  # default mock via orchestrator factory below
    )


def test_scope_guard_is_deterministic():
    assert in_health_scope("今天能不能练腿？")
    assert in_health_scope("昨晚睡差了今天咖啡什么时候喝？")
    assert in_health_scope("can I run today?")
    assert not in_health_scope("帮我写一首诗")
    assert not in_health_scope("今天股市怎么样")


def test_out_of_scope_question_costs_nothing(env):
    qna = make_qna(env)
    r = qna.ask("owner", "帮我写一份商业计划书")
    assert r["scope"] == "OUT_OF_SCOPE"
    assert "超出了我的范围" in r["direct_answer"]
    assert env["adapter"].answer_calls == 0, "no model call for out-of-scope"


def test_answer_is_grounded_in_engine_bundle(env):
    qna = make_qna(env)
    r = qna.ask("owner", "今天能不能练腿？")
    assert r["scope"] == "HEALTH"
    assert r["evidence_ref"]["grounded"] is True
    assert r["evidence_ref"]["analysis_date"] == "2026-08-16"
    assert r["evidence_ref"]["bundle_sha256"]
    assert r["direct_answer"]
    assert len(r["actions"]) <= 3
    assert env["adapter"].answer_calls == 1

    # Sealed audit trail: qa_sessions row with question hash, no chat facts stored as health facts.
    con = sqlite3.connect(env["l6_copy"]); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM qa_sessions WHERE question_text=?", ("今天能不能练腿？",)
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0]["medical_review_state"] == "BYPASSED"


def test_daily_shape_model_output_never_produces_an_empty_answer(env):
    class DailyShapeAdapter:
        model_id = "daily-shape-adapter"

        def answer_question(self, question, bundle, candidates):
            return {
                "reasoning_summary": "今天的状态变化较明显，建议降低训练强度。",
                "recommended_actions": ["降低训练强度"],
            }

    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=DailyShapeAdapter(),
        medical_adapter=None,
    )

    result = qna.ask("owner", "今天能不能练腿？")

    assert result["direct_answer"] == "今天的状态变化较明显，建议降低训练强度。"


def test_insufficient_evidence_is_explicit(env, tmp_path):
    import sqlite3 as s
    empty_l5 = tmp_path / "empty_l5.sqlite3"
    con = s.connect(empty_l5)
    con.execute("CREATE TABLE deviation_analytics (id INTEGER PRIMARY KEY, feature_date TEXT, status TEXT)")
    con.commit(); con.close()
    env["cfg"].l5_db = str(empty_l5)
    qna = make_qna(env)
    r = qna.ask("owner", "我今天适合跑步吗？")
    assert r["direct_answer"] == "目前不能可靠判断。"
    assert r["reason"] and "缺少" in r["reason"]
    assert env["adapter"].answer_calls == 0, "no model call when evidence is insufficient"


def test_medical_routing_only_on_trigger(env):
    qna = make_qna(env)

    class CountingMedical:
        model_id = "mock-medical-v0.1"
        def __init__(self):
            self.calls = 0
        def review(self, bundle, hypothesis_types, question_text=None):
            self.calls += 1
            return {"findings": ["no diagnosis"], "escalation": False}

    med = CountingMedical()
    qna._medical = med

    # Non-medical question: no medical review.
    qna.ask("owner", "今天能不能练腿？")
    assert med.calls == 0

    # Symptom/medical question: sealed trigger policy fires the reviewer exactly once.
    r = qna.ask("owner", "我发烧了，需要去医院吗？")
    assert med.calls == 1
    assert r["medical_review_state"] == "PERFORMED"

    con = sqlite3.connect(env["l6_copy"]); con.row_factory = sqlite3.Row
    performed = con.execute(
        "SELECT * FROM medical_reviews WHERE subject_type='QA' AND review_state='PERFORMED'"
    ).fetchall()
    bypassed = con.execute(
        "SELECT m.* FROM medical_reviews m JOIN qa_sessions q ON q.id=m.subject_id"
        " WHERE m.subject_type='QA' AND q.question_text='今天能不能练腿？'"
    ).fetchall()
    con.close()
    assert len(performed) == 1
    assert bypassed and bypassed[0]["review_state"] == "BYPASSED"


def test_conversation_rollover_after_new_day(env):
    from zoneinfo import ZoneInfo
    qna = make_qna(env)
    tz = ZoneInfo(env["cfg"].timezone_name)
    now_local = datetime(2026, 8, 18, 9, 30, tzinfo=tz)  # morning of the next day
    qna._sleep_exists_for = lambda d: True  # the new day's long sleep has ended

    # Seed a conversation opened "yesterday" relative to the fixed clock (wall-clock free).
    y_iso = (now_local - timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds")

    cur = env["l7"].execute(
        "INSERT INTO conversations (user_id, opened_at_utc, status) VALUES ('owner',?, 'OPEN')",
        (y_iso,),
    )
    conv_id = cur.lastrowid
    env["l7"].execute(
        "INSERT INTO qa_turns (conversation_id,user_id,role,text,created_at_utc)"
        " VALUES (?,?, 'USER','昨天的问题',?)",
        (conv_id, "owner", y_iso),
    )
    env["l7"].commit()

    result = qna.open_or_roll_conversation("owner", now_local=now_local)
    assert result["rolled_over"] is True
    assert result["conversation_id"] != conv_id

    old = env["l7"].execute(
        "SELECT status, boundary_reason FROM conversations WHERE id=?", (conv_id,)
    ).fetchone()
    assert old["status"] == "CLOSED"
    assert old["boundary_reason"] == "date_advanced_and_long_sleep_ended"

    # Same-day continuation: no further rollover (use the new conversation's own open time
    # so the assertion is independent of the wall clock).
    opened_utc = env["l7"].execute(
        "SELECT opened_at_utc FROM conversations WHERE id=?", (result["conversation_id"],)
    ).fetchone()[0]
    cont_time = datetime.fromisoformat(opened_utc).astimezone(tz) + timedelta(minutes=5)
    again = qna.open_or_roll_conversation("owner", now_local=cont_time)
    assert again["rolled_over"] is False
    assert again["conversation_id"] == result["conversation_id"]


def test_no_rollover_when_sleep_not_ended_and_before_noon(env):
    from zoneinfo import ZoneInfo
    qna = make_qna(env)
    tz = ZoneInfo(env["cfg"].timezone_name)
    now_local = datetime(2026, 8, 18, 3, 0, tzinfo=tz)  # 03:00 — user may still be asleep
    qna._sleep_exists_for = lambda d: False

    y_iso = (now_local - timedelta(days=1)).astimezone(timezone.utc).isoformat(timespec="seconds")
    cur = env["l7"].execute(
        "INSERT INTO conversations (user_id, opened_at_utc, status) VALUES ('owner',?, 'OPEN')",
        (y_iso,),
    )
    conv_id = cur.lastrowid
    env["l7"].execute(
        "INSERT INTO qa_turns (conversation_id,user_id,role,text,created_at_utc)"
        " VALUES (?,?, 'USER','昨天的问题',?)",
        (conv_id, "owner", y_iso),
    )
    env["l7"].commit()

    result = qna.open_or_roll_conversation("owner", now_local=now_local)
    assert result["rolled_over"] is False
    assert result["conversation_id"] == conv_id


def test_follow_up_stays_in_conversation(env):
    qna = make_qna(env)
    r1 = qna.ask("owner", "今天能不能练腿？")
    r2 = qna.ask("owner", "那跑步呢？", conversation_id=r1["conversation_id"])
    assert r2["conversation_id"] == r1["conversation_id"]
    state = qna.conversation_state("owner", r1["conversation_id"])
    assert len(state["turns"]) == 4  # 2 user + 2 assistant


def test_chat_history_is_not_health_evidence(env):
    """Asking twice must not accumulate 'facts' in L6 context (four-class separation)."""
    qna = make_qna(env)
    qna.ask("owner", "我最近压力很大怎么办？")
    con = sqlite3.connect(env["l6_copy"]); con.row_factory = sqlite3.Row
    n = con.execute(
        "SELECT COUNT(*) FROM personal_context WHERE raw_text LIKE ?", ("%压力很大%",)
    ).fetchone()[0]
    con.close()
    assert n == 0, "question chatter must not create USER_REPORTED context rows"
