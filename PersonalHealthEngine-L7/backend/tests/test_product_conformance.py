"""Original product-contract regression tests for the final presentation repair."""

from __future__ import annotations

import json

import pytest

from l7.rendering.renderer import evidence_level2, render_today_payload
from l7.services.history import _label
from l7.services.qna import QnAService


DR = {
    "id": 7,
    "analysis_date": "2026-08-24",
    "overall_state": "NOTABLE_CHANGE",
    "primary_hypothesis_type": "RECOVERY_STRAIN",
    "secondary_hypothesis_type": "SLEEP_DEFICIT",
    "confidence": "MODERATE",
    "recommended_actions_json": '["今天降低训练强度"]',
    "medical_review_state": "PERFORMED",
    "reasoning_summary": "近期恢复压力较大，今天适合降低训练强度。",
}


def test_level2_keeps_distinct_sleep_features_instead_of_collapsing_metric():
    bundle = {
        "analysis_date": "2026-08-24",
        "data_date": "2026-08-24",
        "deviations": [
            {
                "metric": "sleep",
                "feature_name": "sleep_source_episode.duration_seconds",
                "deviation_class": "BELOW_TYPICAL_RANGE",
            },
            {
                "metric": "sleep",
                "feature_name": "sleep_source_episode.vendor_stage.rem.proportion",
                "deviation_class": "BELOW_TYPICAL_RANGE",
            },
        ],
    }

    items = evidence_level2(bundle)

    assert any("本次睡眠时长" in item for item in items)
    assert any("REM 睡眠占比" in item for item in items)
    assert len(items) == 2


def test_today_has_canonical_cause_labels_and_presentation_version():
    payload = render_today_payload(
        dr=DR,
        bundle={"data_date": "2026-08-24", "deviations": []},
        product_state="E",
        analysis_date="2026-08-24",
        generated_at_utc="2026-08-24T02:00:00+00:00",
        timezone_name="Asia/Shanghai",
        judgment_updated=False,
        change_note=None,
    )

    assert payload["presentation_contract_version"] >= 2
    assert payload["cause"]["hypothesis_label"] == "恢复压力"
    assert payload["cause"]["secondary"] == {
        "hypothesis_type": "SLEEP_DEFICIT",
        "hypothesis_label": "睡眠不足",
    }
    assert payload["confidence_label"] == "中等"


def test_history_labels_cover_sealed_hypothesis_vocabulary():
    assert _label("RECOVERY_STRAIN") == "恢复压力"
    assert _label("STRESS_RESPONSE") == "压力反应"
    assert _label("ACUTE_ILLNESS_SUSPECTED") != "疑似急性疾病"
    assert _label("UNRECOGNIZED_FUTURE_VALUE") == "暂无法确定原因"


def test_evidence_detail_uses_exact_provenance_ids(env):
    detail = env["service"].evidence_detail("owner")
    assert detail["metrics"]

    for metric in detail["metrics"]:
        assert isinstance(metric["l5_deviation_id"], int)
        assert isinstance(metric["l3_feature_id"], int)
        assert isinstance(metric["l4_baseline_id"], int)
        assert metric["deviations"] == [
            {
                **metric["deviations"][0],
                "id": metric["l5_deviation_id"],
                "l3_feature_id": metric["l3_feature_id"],
                "l4_baseline_id": metric["l4_baseline_id"],
            }
        ]
        assert metric["feature_date"] == metric["deviations"][0]["feature_date"]
        assert metric["feature_label"]
        assert metric["freshness_label"]
        assert metric["current_value_display"]
        assert metric["baseline_value_display"]
        assert metric["feature_label"] in metric["text"]
        assert metric["feature_date"] in metric["text"]


def test_same_judgment_refreshes_time_without_rephrasing(env, monkeypatch):
    first = env["orch"].evaluate("owner", "contract-test").today_payload
    stable_keys = ("headline", "cause", "actions", "evidence_level2", "version_id")

    import l7.engine.orchestrator as orchestrator_module

    refreshed_at = "2026-08-24T12:34:56+00:00"
    monkeypatch.setattr(orchestrator_module, "utc_now", lambda: refreshed_at)
    second = env["orch"].evaluate("owner", "contract-test-refresh").today_payload

    for key in stable_keys:
        assert second[key] == first[key]
    assert second["updated_at_utc"] == refreshed_at
    assert second["updated_at_utc"] != first["updated_at_utc"]
    assert second["judgment_updated"] is False


def test_today_embeds_exact_dated_evidence_facts(env):
    payload = env["orch"].evaluate("owner", "contract-evidence").today_payload

    assert payload["evidence"]
    assert payload["evidence_level2"] == [item["text"] for item in payload["evidence"][:6]]
    for item in payload["evidence"]:
        assert isinstance(item["l5_deviation_id"], int)
        assert isinstance(item["l3_feature_id"], int)
        assert isinstance(item["l4_baseline_id"], int)
        assert item["feature_date"] in item["text"]
        assert item["freshness_label"] in item["text"]


def test_legacy_english_presentation_is_appended_without_judgment_change(env):
    first = env["orch"].evaluate("owner", "seed-legacy").today_payload
    row = env["l7"].execute(
        "SELECT id, rendered_json, signature_sha256 FROM today_versions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    legacy = json.loads(row["rendered_json"])
    legacy.pop("presentation_contract_version", None)
    legacy["cause"]["text"] = "近期恢复压力较大。Recovery strain is the most likely explanation."
    legacy["actions"] = ["今天休息。Reduce training intensity today."]
    env["l7"].execute(
        "UPDATE today_versions SET rendered_json=? WHERE id=?",
        (json.dumps(legacy, ensure_ascii=False), row["id"]),
    )
    env["l7"].commit()

    translation_calls = []

    def translate(summary, actions):
        translation_calls.append((summary, actions))
        return {
            "reasoning_summary": "最可能与近期恢复压力有关。",
            "recommended_actions": ["今天降低训练强度。"],
        }

    env["adapter"].translate_product_copy = translate
    repaired = env["orch"].evaluate("owner", "repair-presentation")
    versions = env["l7"].execute(
        "SELECT id, signature_sha256, judgment_updated FROM today_versions ORDER BY id"
    ).fetchall()

    assert repaired.today_payload["version_id"] != first["version_id"]
    assert repaired.today_payload["presentation_contract_version"] >= 2
    assert repaired.today_payload["cause"]["text"] == "最可能与近期恢复压力有关。"
    assert repaired.today_payload["actions"] == ["今天降低训练强度。"]
    assert repaired.judgment_updated is False
    assert repaired.model_calls == 1
    assert len(translation_calls) == 1
    assert len(versions) == 2
    assert versions[0]["signature_sha256"] == versions[1]["signature_sha256"]
    assert versions[1]["judgment_updated"] == 0


def test_failed_legacy_translation_is_not_sealed_and_can_retry(env):
    first = env["orch"].evaluate("owner", "seed-retry").today_payload
    row = env["l7"].execute(
        "SELECT id, rendered_json FROM today_versions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    legacy = json.loads(row["rendered_json"])
    legacy.pop("presentation_contract_version", None)
    legacy["cause"]["text"] = "Recovery strain."
    legacy["actions"] = ["Rest today."]
    env["l7"].execute(
        "UPDATE today_versions SET rendered_json=? WHERE id=?",
        (json.dumps(legacy, ensure_ascii=False), row["id"]),
    )
    env["l7"].commit()

    attempts = []

    def fail_translation(summary, actions):
        attempts.append("failed")
        raise RuntimeError("temporary model outage")

    env["adapter"].translate_product_copy = fail_translation
    degraded = env["orch"].evaluate("owner", "repair-failed")
    assert degraded.today_payload["version_id"] == first["version_id"]
    assert degraded.today_payload["actions"] == []
    assert len(env["l7"].execute("SELECT id FROM today_versions").fetchall()) == 1

    env["adapter"].translate_product_copy = lambda summary, actions: {
        "reasoning_summary": "近期恢复压力较大。",
        "recommended_actions": ["今天降低训练强度。"],
    }
    repaired = env["orch"].evaluate("owner", "repair-retry")
    assert repaired.today_payload["version_id"] != first["version_id"]
    assert len(env["l7"].execute("SELECT id FROM today_versions").fetchall()) == 2
    assert attempts == ["failed"]


def test_qna_keeps_direct_answer_and_reason_separate(env):
    class StructuredAdapter:
        model_id = "structured-zh-test"

        def answer_question(self, question, bundle, candidates):
            return {
                "answer_text": "今天不建议进行高强度训练。",
                "reasoning_summary": "恢复相关指标偏离个人近期基线。",
                "recommended_actions": ["改为轻松活动"],
            }

    qna = QnAService(
        env["cfg"], env["l7"], env["orch"].bridge,
        reasoning_adapter=StructuredAdapter(), medical_adapter=None,
    )

    result = qna.ask("owner", "今天能不能进行高强度训练？")

    assert result["direct_answer"] == "今天不建议进行高强度训练。"
    assert result["reason"] == "恢复相关指标偏离个人近期基线。"
    assert result["actions"] == ["改为轻松活动"]


def test_l7_product_deepseek_adapter_requires_simplified_chinese(monkeypatch):
    from l7.upstream.l6_bridge import ProductDeepSeekReasoningAdapter

    adapter = ProductDeepSeekReasoningAdapter(api_key="test-only")
    captured = {}

    def fake_chat(system, user, reasoning_effort=None):
        captured["system"] = system
        return json.dumps({
            "primary_hypothesis_type": "RECOVERY_STRAIN",
            "secondary_hypothesis_type": None,
            "confidence": "LOW",
            "recommended_actions": ["今天降低训练强度"],
            "reasoning_summary": "近期恢复压力较大。",
        }, ensure_ascii=False)

    monkeypatch.setattr(adapter, "_chat", fake_chat)
    adapter.reason_daily({}, [{"hypothesis_type": "RECOVERY_STRAIN"}])

    assert adapter.contract_version
    assert "简体中文" in captured["system"]
    assert "English" in captured["system"]


def test_l7_product_adapter_rejects_mixed_english_or_raw_enum(monkeypatch):
    from l7.upstream.l6_bridge import ProductDeepSeekReasoningAdapter

    adapter = ProductDeepSeekReasoningAdapter(api_key="test-only")
    monkeypatch.setattr(
        adapter,
        "_chat",
        lambda *args, **kwargs: json.dumps({
            "primary_hypothesis_type": "RECOVERY_STRAIN",
            "secondary_hypothesis_type": None,
            "confidence": "LOW",
            "recommended_actions": ["今天降低训练强度"],
            "reasoning_summary": "近期恢复压力较大。Reduce training for RECOVERY_STRAIN.",
        }, ensure_ascii=False),
    )

    with pytest.raises(Exception, match="Simplified-Chinese"):
        adapter.reason_daily({}, [{"hypothesis_type": "RECOVERY_STRAIN"}])
