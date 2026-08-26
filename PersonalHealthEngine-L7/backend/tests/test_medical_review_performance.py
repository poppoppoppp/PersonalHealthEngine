"""Fast routing, compact critic input, and exact-cache performance contracts."""

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor

from conftest import CountingMockReasoningAdapter
from l7.engine.qna_orchestration import (
    build_medical_review_bundle,
    deterministic_fast_classification,
    medical_review_cache_key,
)
from l7.medical_cache import MedicalReviewCache
from l7.services.qna import QnAService


def strict_approved():
    return {
        "review_status": "APPROVED",
        "medical_concerns": [],
        "causality_concerns": [],
        "missing_safety_considerations": [],
        "unsafe_actions": [],
        "required_changes": [],
        "escalation_reason": None,
        "review_summary": "已完成审查",
    }


def make_qna(env, *, reasoning=None, medical=None):
    return QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=reasoning or env["adapter"], medical_adapter=medical,
    )


def test_fixed_product_meta_and_registered_health_data_skip_semantic_model(env):
    qna = make_qna(env)
    assert qna.ask("owner", "你是谁？")["scope"] == "PRODUCT_META"
    assert qna.ask("owner", "这个产品能做什么？")["scope"] == "PRODUCT_META"
    assert qna.ask("owner", "昨晚睡了多久？")["scope"] == "HEALTH_DATA"
    assert qna.ask("owner", "最近7天平均步数是多少？")["scope"] == "HEALTH_DATA"
    assert qna.ask("owner", "我最近七天的静息心率平均值是多少？")["scope"] == "HEALTH_DATA"
    assert env["adapter"].semantic_calls == 0


def test_ambiguous_question_falls_back_to_semantic_model(env):
    qna = make_qna(env)
    assert deterministic_fast_classification("我最近感觉怎么样？") is None
    qna.ask("owner", "我最近感觉怎么样？")
    assert env["adapter"].semantic_calls == 1


def test_medical_review_bundle_contains_only_resolved_evidence_and_safety_state():
    evidence = {
        "analysis_date": "2026-08-24",
        "overall_state": "NOTABLE_CHANGE",
        "today": {"medical_attention": {"state": "REQUIRED"}, "large": "x" * 5000},
        "recent_context": [
            {"context_type": "HEADACHE", "raw_text": "private symptom"},
            {"context_type": "TRAVEL", "raw_text": "unrelated trip"},
        ],
        "missing_evidence": ["temperature"],
        "evidence_catalog": {
            "today.overall_state": "NOTABLE_CHANGE",
            "deviations.0": "sleep below baseline",
            "deviations.1": "steps above baseline",
        },
        "deviations": [{"huge": "y" * 5000}],
        "recent_deviations": [{"huge": "z" * 5000}],
    }
    candidate = {
        "direct_answer": "今天降低训练强度。",
        "reason": "睡眠偏低。",
        "recommended_actions": ["先休息"],
        "confidence": "LOW",
        "evidence_refs": ["deviations.0"],
        "medical_claims": [],
        "uncertainties": [],
    }
    compact = build_medical_review_bundle(
        evidence, candidate, [], today_medical_state="REQUIRED",
    )
    serialized = json.dumps(compact, ensure_ascii=False)
    assert compact["schema"] == "phe.medical_review/v1"
    assert compact["resolved_evidence"] == {
        "deviations.0": "sleep below baseline",
    }
    assert [item["context_type"] for item in compact["safety_context"]] == ["HEADACHE"]
    assert "personal_evidence_bundle" not in compact
    assert len(serialized.encode("utf-8")) < 2500


def test_exact_cache_key_changes_for_every_safety_input():
    base = {
        "question_representation": "今天适合训练吗",
        "classification": {"scope": "HEALTH_DECISION", "medical_consequence": "MODERATE"},
        "candidate": {"direct_answer": "今天降低强度。"},
        "resolved_evidence_hash": "evidence-a",
        "medical_state": {"today": "REQUIRED"},
        "model_artifact_hash": "model-a",
        "critic_prompt_version": "critic-v1",
        "schema_version": "phe.medical_review/v1",
    }
    original = medical_review_cache_key(**base)
    replacements = {
        "question_representation": "明天适合训练吗",
        "classification": {"scope": "HEALTH_DECISION", "medical_consequence": "HIGH"},
        "candidate": {"direct_answer": "今天休息。"},
        "resolved_evidence_hash": "evidence-b",
        "medical_state": {"today": "UNAVAILABLE"},
        "model_artifact_hash": "model-b",
        "critic_prompt_version": "critic-v2",
        "schema_version": "phe.medical_review/v2",
    }
    for field, replacement in replacements.items():
        changed = copy.deepcopy(base)
        changed[field] = replacement
        assert medical_review_cache_key(**changed) != original, field


def test_identical_valid_medical_reviews_hit_exact_cache(env):
    class CountingMedical:
        model_id = "mock-medical-cache-v1"

        def __init__(self):
            self.calls = 0

        def review(self, *args, **kwargs):
            self.calls += 1
            return strict_approved()

    medical = CountingMedical()
    qna = make_qna(env, reasoning=CountingMockReasoningAdapter(), medical=medical)
    first = qna.ask("owner", "今天适合高强度训练吗？")
    second = qna.ask("owner", "今天适合高强度训练吗？")
    assert first["medical_review_state"] == second["medical_review_state"] == "PERFORMED"
    assert medical.calls == 1


def test_concurrent_identical_cache_misses_coalesce(env):
    cache = MedicalReviewCache(env["l7"])
    calls = 0

    def review():
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return strict_approved()

    def invoke():
        return cache.get_or_review("same-key", review)[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))
    assert [result["review_status"] for result in results] == ["APPROVED", "APPROVED"]
    assert calls == 1


def test_corrupt_cache_fails_closed_without_calling_model(env):
    cache = MedicalReviewCache(env["l7"])
    env["l7"].execute(
        "INSERT INTO medical_review_cache "
        "(cache_key,response_json,model_artifact_hash,created_at_utc,last_used_at_utc,hit_count) "
        "VALUES ('corrupt','{}','model','2026-08-24T00:00:00+00:00',"
        "'2026-08-24T00:00:00+00:00',0)"
    )
    env["l7"].commit()
    called = False

    def forbidden():
        nonlocal called
        called = True
        return strict_approved()

    result, outcome = cache.get_or_review("corrupt", forbidden)
    assert outcome == "CORRUPT"
    assert result["review_status"] == "UNAVAILABLE"
    assert called is False
