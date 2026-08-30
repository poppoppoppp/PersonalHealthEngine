"""Phase E tests: Q&A grounding, medical routing, conversation rollover, scope guard."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from l7.services.qna import QnAService, in_health_scope
from conftest import CountingMockReasoningAdapter, PROD_L6  # noqa: F401


def force_review_hint(adapter):
    """Pin the semantic review hint on so mechanism tests reach the reviewer even
    though the narrowed consequence gate bypasses plain workout questions."""
    original = adapter.classify_question

    def classify(question, conversation_semantics):
        payload = original(question, conversation_semantics)
        return {**payload, "needs_medical_review": True}

    adapter.classify_question = classify


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


def test_semantic_scope_routes_without_keyword_authority(env):
    qna = make_qna(env)

    cases = {
        "今天要散步吗？": "HEALTH_DECISION",
        "出去转几圈合适吗？": "HEALTH_DECISION",
        "今天少动点好吗？": "HEALTH_DECISION",
        "你是谁？": "PRODUCT_META",
        "帮我写Python": "OUT_OF_SCOPE",
    }

    for question, expected_scope in cases.items():
        result = qna.ask("owner", question)
        assert result["scope"] == expected_scope

    assert env["adapter"].semantic_calls == len(cases) - 1
    assert env["adapter"].answer_calls == 3


def test_product_meta_is_fixed_and_costs_no_health_reasoning(env):
    qna = make_qna(env)

    result = qna.ask("owner", "你是谁？")

    assert result["scope"] == "PRODUCT_META"
    assert "个人健康决策助手" in result["direct_answer"]
    assert result["medical_review_state"] == "BYPASSED"
    assert env["adapter"].answer_calls == 0


def test_health_data_last_night_sleep_uses_exact_l3_values(env):
    qna = make_qna(env)

    result = qna.ask("owner", "我昨晚睡了多久？")

    assert result["scope"] == "HEALTH_DATA"
    assert result["medical_review_state"] == "BYPASSED"
    assert result["evidence_ref"]["data_authority"] == "L3"
    assert result["evidence_ref"]["feature_name"] == (
        "sleep_source_episode.vendor_sleep_like_duration_seconds"
    )
    assert result["evidence_ref"]["data_date"] == "2026-08-16"
    assert result["evidence_ref"]["source_count"] == 2
    assert "7小时58分钟" in result["direct_answer"]
    assert "4小时26分钟" in result["direct_answer"]
    assert env["adapter"].answer_calls == 0


def test_health_data_average_preserves_source_isolation(env):
    qna = make_qna(env)

    result = qna.ask("owner", "最近7天平均睡眠时间是多少？")

    assert result["scope"] == "HEALTH_DATA"
    assert result["evidence_ref"]["aggregation"] == "AVERAGE"
    assert result["evidence_ref"]["source_count"] == 2
    assert "分别" in result["direct_answer"]
    assert env["adapter"].answer_calls == 0


def test_health_data_steps_average_is_engine_calculated(env):
    qna = make_qna(env)

    result = qna.ask("owner", "最近7天平均步数是多少？")

    assert result["scope"] == "HEALTH_DATA"
    assert result["evidence_ref"]["feature_name"] == "steps.daily.sum"
    assert result["evidence_ref"]["aggregation"] == "AVERAGE"
    assert "步" in result["direct_answer"]
    assert env["adapter"].answer_calls == 0


def test_health_data_resting_heart_rate_trend_uses_l3_series(env):
    qna = make_qna(env)

    result = qna.ask("owner", "最近静息心率趋势怎么样？")

    assert result["scope"] == "HEALTH_DATA"
    assert result["evidence_ref"]["feature_name"] == "resting_heart_rate.daily.value"
    assert result["evidence_ref"]["aggregation"] == "TREND"
    assert "从51次/分钟到57次/分钟，上升6次/分钟" in result["direct_answer"]
    assert env["adapter"].answer_calls == 0


def test_semantic_follow_up_uses_bounded_conversation_context(env):
    qna = make_qna(env)
    first = qna.ask("owner", "今天适合跑步吗？")

    follow_up = qna.ask("owner", "那半小时呢？", conversation_id=first["conversation_id"])

    assert first["scope"] == "HEALTH_DECISION"
    assert follow_up["scope"] == "HEALTH_DECISION"
    assert follow_up["conversation_id"] == first["conversation_id"]
    assert env["adapter"].semantic_calls == 2


def test_answer_is_grounded_in_engine_bundle(env):
    force_review_hint(env['adapter'])
    qna = make_qna(env)
    r = qna.ask("owner", "今天能不能练腿？")
    assert r["scope"] == "HEALTH_DECISION"
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
    assert rows[0]["medical_review_state"] == "PERFORMED"


def test_question_specific_bundle_excludes_unrelated_metrics(env):
    qna = make_qna(env)

    qna.ask("owner", "今天要散步吗？")

    bundle = env["adapter"].last_answer_bundle
    assert bundle["schema"] == "phe.qna.evidence/v2"
    assert bundle["intent"] == "ACTIVITY_RECOMMENDATION"
    assert bundle["evidence_catalog"]
    allowed = {"sleep", "steps", "resting_heart_rate"}
    assert {item["metric"] for item in bundle["deviations"]} <= allowed
    assert not any(item["metric"] in {"calories", "spo2"} for item in bundle["deviations"])


def test_unsafe_or_untraceable_candidate_is_never_exposed(env):
    class UnsafeCandidateAdapter(CountingMockReasoningAdapter):
        def answer_question(self, question, bundle, candidates):
            self.answer_calls += 1
            self.last_answer_bundle = bundle
            return {
                "answer_text": "你发烧了，而且肯定得了心脏病，今天不要散步。",
                "direct_answer": "你发烧了，而且肯定得了心脏病，今天不要散步。",
                "reason": "这是确定的诊断。",
                "reasoning_summary": "这是确定的诊断。",
                "recommended_actions": ["立即停下所有活动"],
                "confidence": "HIGH",
                "evidence_refs": ["evidence.does_not_exist"],
                "medical_claims": ["用户肯定得了心脏病"],
                "uncertainties": [],
            }

    adapter = UnsafeCandidateAdapter()
    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=adapter,
        medical_adapter=None,
    )

    result = qna.ask("owner", "今天要散步吗？")

    assert "你发烧了" not in result["direct_answer"]
    assert "心脏病" not in result["direct_answer"]
    assert result["medical_review_state"] != "BYPASSED"


def test_english_only_candidate_is_rejected_before_product_output(env):
    class EnglishCandidateAdapter(CountingMockReasoningAdapter):
        def answer_question_candidate(self, question, bundle, candidates):
            return {
                "direct_answer": "Take a walk today.",
                "reason": "Your current evidence supports light activity.",
                "recommended_actions": ["Stop if fatigue gets worse."],
                "confidence": "LOW",
                "evidence_refs": [next(iter(bundle["evidence_catalog"]))],
                "medical_claims": [],
                "uncertainties": [],
            }

    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=EnglishCandidateAdapter(), medical_adapter=None,
    )

    result = qna.ask("owner", "今天要散步吗？")

    assert "Take a walk" not in result["direct_answer"]
    assert any("\u3400" <= char <= "\u9fff" for char in result["direct_answer"])


def strict_review(status, required_changes=None):
    return {
        "review_status": status,
        "medical_concerns": [],
        "causality_concerns": [],
        "missing_safety_considerations": [],
        "unsafe_actions": [],
        "required_changes": required_changes or [],
        "escalation_reason": "存在紧急红旗信号" if status == "ESCALATE" else None,
        "review_summary": "已完成安全审查",
    }


def test_medgemma_receives_candidate_after_deepseek(env):
    force_review_hint(env['adapter'])
    events = []

    class OrderedReasoning(CountingMockReasoningAdapter):
        def answer_question_candidate(self, question, bundle, candidates):
            events.append("deepseek_candidate")
            return super().answer_question_candidate(question, bundle, candidates)

    class CapturingMedical:
        model_id = "mock-medical-v2"

        def __init__(self):
            self.review_bundle = None

        def review(self, review_bundle, hypothesis_types, question_text=None):
            events.append("medgemma_review")
            self.review_bundle = review_bundle
            return strict_review("APPROVED")

    reasoning = OrderedReasoning()
    force_review_hint(reasoning)
    medical = CapturingMedical()
    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=reasoning,
        medical_adapter=medical,
    )

    result = qna.ask("owner", "今天要散步吗？")

    assert events == ["deepseek_candidate", "medgemma_review"]
    assert medical.review_bundle["candidate"]["direct_answer"]
    assert medical.review_bundle["schema"] == "phe.medical_review/v1"
    assert "personal_evidence_bundle" not in medical.review_bundle
    assert result["medical_review_state"] == "PERFORMED"


def test_medical_finalizer_paths(env):
    force_review_hint(env['adapter'])
    expected = {
        "APPROVED": "candidate",
        "APPROVED_WITH_CHANGES": "revised",
        "REJECTED": "当前不能基于现有证据可靠给出这个结论。",
        "ESCALATE": "尽快寻求专业医疗帮助",
        "UNAVAILABLE": "未经审查的建议",
    }

    for status, expected_text in expected.items():
        class RevisionReasoning(CountingMockReasoningAdapter):
            def __init__(self):
                super().__init__()
                self.revision_calls = 0

            def answer_question_candidate(self, question, bundle, candidates):
                candidate = super().answer_question_candidate(question, bundle, candidates)
                candidate["direct_answer"] = "candidate：今天可以轻松散步。"
                candidate["reason"] = "candidate：依据当前个人证据。"
                return candidate

            def revise_question_candidate(self, question, bundle, candidate, required_changes):
                self.revision_calls += 1
                revised = dict(candidate)
                revised["direct_answer"] = "revised：今天只建议短时间轻松散步。"
                revised["reason"] = "revised：已按安全审查降低建议强度。"
                return revised

        class StatusMedical:
            model_id = f"mock-medical-v2-{status}"

            def review(self, review_bundle, hypothesis_types, question_text=None):
                return strict_review(status, ["降低活动强度"] if status == "APPROVED_WITH_CHANGES" else [])

        reasoning = RevisionReasoning()
        force_review_hint(reasoning)
        qna = QnAService(
            env["cfg"], env["l7"], env["orch"].bridge,
            reasoning_adapter=reasoning,
            medical_adapter=StatusMedical(),
        )
        result = qna.ask("owner", "今天要散步吗？")
        assert expected_text in result["direct_answer"]
        assert reasoning.revision_calls == (1 if status == "APPROVED_WITH_CHANGES" else 0)
        assert result["medical_review_state"] == ("UNAVAILABLE" if status == "UNAVAILABLE" else "PERFORMED")


def test_required_medical_review_unavailable_fails_closed(env):
    force_review_hint(env['adapter'])

    class UnavailableMedical:
        model_id = "medgemma1.5"

        def review(self, review_bundle, hypothesis_types, question_text=None):
            raise TimeoutError("synthetic timeout")

    reasoning = CountingMockReasoningAdapter()
    force_review_hint(reasoning)
    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=reasoning,
        medical_adapter=UnavailableMedical(),
    )

    result = qna.ask("owner", "今天适合高强度训练吗？")

    assert result["medical_review_state"] == "UNAVAILABLE"
    assert "未经审查的建议" in result["direct_answer"]
    assert not any("高强度" in action for action in result["actions"])


def test_qna_audit_records_sanitized_stage_order(env):
    force_review_hint(env['adapter'])

    class ApprovedMedical:
        model_id = "mock-medical-v2"

        def review(self, review_bundle, hypothesis_types, question_text=None):
            return strict_review("APPROVED")

    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=env["adapter"],
        medical_adapter=ApprovedMedical(),
    )

    qna.ask("owner", "今天要散步吗？")

    row = env["l7"].execute("SELECT * FROM qna_audits ORDER BY id DESC LIMIT 1").fetchone()
    classification = json.loads(row["semantic_classification_json"])
    stages = json.loads(row["stage_events_json"])
    assert row["semantic_classifier_model"] == "mock-reasoning-v0.1"
    assert classification["scope"] == "HEALTH_DECISION"
    assert row["reasoning_model"] == "mock-reasoning-v0.1"
    assert row["reasoning_called"] == 1
    assert row["medical_review_required"] == 1
    assert row["medical_model"] == "mock-medical-v2"
    assert row["medical_review_state"] == "PERFORMED"
    assert row["finalization_path"] == "APPROVED"
    assert stages == ["SEMANTIC", "EVIDENCE", "REASONING", "MEDICAL", "FINALIZER"]
    serialized = json.dumps(dict(row), ensure_ascii=False)
    assert "API_KEY" not in serialized
    assert "thinking_trace" not in serialized


def test_non_reasoning_routes_are_audited_without_health_calls(env):
    qna = make_qna(env)

    qna.ask("owner", "你是谁？")
    qna.ask("owner", "我昨晚睡了多久？")
    qna.ask("owner", "法国首都是什么？")

    rows = env["l7"].execute(
        "SELECT reasoning_called,medical_review_required,finalization_path "
        "FROM qna_audits ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (0, 0, "PRODUCT_META_FIXED"),
        (0, 0, "HEALTH_DATA_ENGINE"),
        (0, 0, "OUT_OF_SCOPE_FIXED"),
    ]


def test_explicit_context_statement_uses_formal_context_write(env):
    from l7.services.context import ContextService

    context_service = ContextService(
        env["cfg"], env["l7"], env["orch"].bridge, env["orch"],
        reasoning_adapter=env["adapter"],
    )
    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=env["adapter"],
        medical_adapter=None,
        context_writer=context_service,
    )

    result = qna.ask("owner", "我昨晚其实两点才睡")

    assert result["scope"] == "HEALTH_CONTEXT"
    assert "后台" in result["direct_answer"]
    con = sqlite3.connect(env["l6_copy"])
    row = con.execute(
        "SELECT context_type,raw_text,source FROM personal_context "
        "WHERE raw_text=? AND status='CURRENT'",
        ("我昨晚其实两点才睡",),
    ).fetchone()
    con.close()
    assert row is None
    job = env["l7"].execute(
        "SELECT kind,status FROM durable_jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert tuple(job) == ("CONTEXT_INGEST", "PENDING")


def test_headache_statement_is_persisted_as_user_reported_context(env):
    from l7.services.context import ContextService

    class HeadacheAdapter(CountingMockReasoningAdapter):
        def extract_context(self, text, today):
            if "头疼" in text:
                return [{"context_type": "HEADACHE", "context_date": today}]
            return super().extract_context(text, today)

    adapter = HeadacheAdapter()

    context_service = ContextService(
        env["cfg"], env["l7"], env["orch"].bridge, env["orch"],
        reasoning_adapter=adapter,
    )
    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=adapter, context_writer=context_service,
    )

    result = qna.ask("owner", "我今天有点头疼")

    assert result["scope"] == "HEALTH_CONTEXT"
    con = sqlite3.connect(env["l6_copy"])
    row = con.execute(
        "SELECT context_type,source FROM personal_context WHERE raw_text=? AND status='CURRENT'",
        ("我今天有点头疼",),
    ).fetchone()
    con.close()
    assert row is None
    job = env["l7"].execute(
        "SELECT kind,status FROM durable_jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert tuple(job) == ("CONTEXT_INGEST", "PENDING")


def test_conversation_cannot_be_reused_by_another_user(env):
    env["l7"].execute("INSERT INTO users (id,created_at_utc) VALUES ('other','2026-08-24T00:00:00Z')")
    cur = env["l7"].execute(
        "INSERT INTO conversations (user_id,opened_at_utc,status) VALUES ('other','2026-08-24T00:00:00Z','OPEN')"
    )
    env["l7"].commit()

    qna = make_qna(env)

    import pytest
    with pytest.raises(ValueError, match="conversation"):
        qna.ask("owner", "今天要散步吗？", conversation_id=cur.lastrowid)


def test_legacy_daily_shape_is_rejected_without_empty_answer(env):
    class DailyShapeAdapter:
        model_id = "daily-shape-adapter"

        def classify_question(self, question, conversation_semantics):
            return {
                "scope": "HEALTH_DECISION",
                "intent": "ACTIVITY_RECOMMENDATION",
                "decision_type": "PHYSICAL_ACTIVITY",
                "relevant_domains": ["ACTIVITY"],
                "relevant_metrics": ["SLEEP_DURATION"],
                "requires_personal_evidence": True,
                "time_range": "CURRENT",
                "aggregation": None,
                "medical_consequence": "MODERATE",
                "needs_medical_review": True,
                "potential_context": False,
                "context_write": "NONE",
                "reason": "test",
            }

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

    assert result["direct_answer"] == "当前不能基于现有证据可靠给出这个结论。"
    assert result["medical_review_state"] == "UNAVAILABLE"


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


def test_medical_consequence_gate_reviews_actions_and_bypasses_data(env):
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

    # Deterministic data lookup: no medical review.
    qna.ask("owner", "我昨晚睡了多久？")
    assert med.calls == 0

    # A plain workout question on an ordinary day carries no medical signal: the gate
    # skips the local reviewer instead of paying its minutes-long latency for nothing.
    workout = qna.ask("owner", "今天能不能练腿？")
    assert med.calls == 0
    assert workout["medical_review_state"] == "BYPASSED"

    # Symptom/medical question also fires the reviewer.
    r = qna.ask("owner", "我发烧了，需要去医院吗？")
    assert med.calls == 1
    assert r["medical_review_state"] == "PERFORMED"

    con = sqlite3.connect(env["l6_copy"]); con.row_factory = sqlite3.Row
    performed = con.execute(
        "SELECT * FROM medical_reviews WHERE subject_type='QA' AND review_state='PERFORMED'"
    ).fetchall()
    con.close()
    assert len(performed) == 1


def test_medical_gate_still_reviews_when_safety_signals_present(env):
    """The narrowed gate keeps its net: reported symptom contexts, medical claims in the
    candidate, or a medical-flagged Today each force the reviewer back in."""
    from l7.engine.qna_orchestration import medical_consequence_gate

    classification = {
        "medical_consequence": "MODERATE",
        "needs_medical_review": False,
        "decision_type": "PHYSICAL_ACTIVITY",
    }
    plain_candidate = {"medical_claims": []}
    claiming_candidate = {"medical_claims": ["你的静息心率升高可能与发热相关"]}

    # Plain workout question, no safety context: bypass.
    required, reasons = medical_consequence_gate(
        classification, "BYPASSED", [], None, plain_candidate, [],
        has_medical_safety_context=False,
    )
    assert required is False

    # Same question with a reported fever context: review.
    required, reasons = medical_consequence_gate(
        classification, "BYPASSED", [], None, plain_candidate, [],
        has_medical_safety_context=True,
    )
    assert required is True and "moderate_consequence_with_safety_signals" in reasons

    # Candidate volunteering medical claims: review even without context.
    required, _ = medical_consequence_gate(
        classification, "BYPASSED", [], None, claiming_candidate, [],
        has_medical_safety_context=False,
    )
    assert required is True

    # High consequence or a medical-flagged Today: review regardless.
    required, _ = medical_consequence_gate(
        {**classification, "medical_consequence": "HIGH"},
        "BYPASSED", [], None, plain_candidate, [],
    )
    assert required is True
    required, _ = medical_consequence_gate(
        classification, "BYPASSED", [], "PERFORMED", plain_candidate, [],
    )
    assert required is True


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


def test_revision_failure_falls_back_to_original_with_critic_changes(env):
    """审查已批准"仅需修改"时，修订稿反复不合格不应吞掉整个回答：
    退回原候选，并把审查要求的修改追加为谨慎项。"""
    force_review_hint(env["adapter"])
    qna = make_qna(env)
    force_review_hint(env["adapter"])

    class StrictMedical:
        model_id = "mock-medical-strict"

        def review(self, bundle, hypothesis_types, question_text=None):
            return {
                "review_status": "APPROVED_WITH_CHANGES",
                "medical_concerns": [],
                "causality_concerns": [],
                "missing_safety_considerations": [],
                "unsafe_actions": [],
                "required_changes": ["补充一条：如出现头晕立即停止"],
                "escalation_reason": None,
                "review_summary": "内容可发，但需补充谨慎项。",
            }

    class BadReviser(CountingMockReasoningAdapter):
        def __init__(self):
            super().__init__()
            self.revision_calls = 0

        def answer_question_candidate(self, question, bundle, candidates):
            base = super().answer_question_candidate(question, bundle, candidates)
            base["direct_answer"] = "基于你的睡眠数据，今天适合轻度活动。"
            base["reason"] = "你的睡眠时长低于个人基线。"
            return base

        def revise_question_candidate(self, *args, **kwargs):
            self.revision_calls += 1
            return {"direct_answer": 123}  # 反复不合格

    reviser = BadReviser()
    force_review_hint(reviser)
    qna._reasoning = reviser
    qna._medical = StrictMedical()

    r = qna.ask("owner", "今天能不能练腿？")

    assert reviser.revision_calls == 3, "bounded retries before fallback"
    assert r["direct_answer"] == "基于你的睡眠数据，今天适合轻度活动。"
    assert any("头晕立即停止" in a for a in r["actions"]), "critic changes appended"
    assert r["medical_review_state"] == "PERFORMED"
