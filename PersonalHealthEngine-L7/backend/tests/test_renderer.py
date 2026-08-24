"""Unit tests: deterministic renderer, semantic stability (§10), no-score audit (§13)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l7.rendering.renderer import (
    evidence_level2,
    judgment_signature,
    render_today_payload,
)

BUNDLE = {
    "analysis_date": "2026-08-16",
    "data_date": "2026-08-16",
    "deviations": [
        {"metric": "sleep", "feature_name": "sleep_source_episode.vendor_stage.deep.proportion",
         "deviation_class": "BELOW_TYPICAL_RANGE"},
        {"metric": "resting_heart_rate", "feature_name": "resting_heart_rate.daily.value",
         "deviation_class": "ABOVE_TYPICAL_RANGE"},
        {"metric": "heart_rate", "feature_name": "heart_rate.daily.mean",
         "deviation_class": "WITHIN_TYPICAL_RANGE"},
    ],
    "persistence": [
        {"feature_name": "resting_heart_rate.daily.value",
         "persistence_class": "PERSISTENT_ABOVE_TYPICAL",
         "consecutive_above_typical": 2, "consecutive_below_typical": 0},
    ],
    "recent_context": [{"context_type": "LATE_SLEEP", "context_date": "2026-08-16"}],
}

DR = {
    "id": 1,
    "analysis_date": "2026-08-16",
    "overall_state": "NOTABLE_CHANGE",
    "primary_hypothesis_type": "SLEEP_DEFICIT",
    "secondary_hypothesis_type": None,
    "confidence": "LOW",
    "recommended_actions_json": '["今天优先补充睡眠","暂缓高强度训练","避免咖啡因在下午之后摄入"]',
    "medical_review_state": "BYPASSED",
    "reasoning_summary": "最近可能因为睡眠不足，身体压力指标有些升高。",
}


def render(product_state="C"):
    return render_today_payload(
        dr=DR, bundle=BUNDLE, product_state=product_state,
        analysis_date="2026-08-16", generated_at_utc="2026-08-17T02:00:00+00:00",
        timezone_name="Asia/Shanghai", judgment_updated=False, change_note=None,
    )


def test_renderer_is_a_pure_function():
    assert render() == render()


def test_semantic_stability_identical_judgment_identical_text():
    p1 = render()
    p2 = render()
    for key in ("product_state", "product_state_label", "headline", "cause", "actions",
                "confidence", "evidence_level2"):
        assert p1[key] == p2[key], key


def test_no_score_fields_exist():
    payload = render()
    forbidden = ("score", "readiness", "health_score", "rating", "stars", "grade", "traffic_light")
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert not any(f in str(k).lower() for f in forbidden), f"forbidden field {k}"
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(payload)


def test_information_order_normal():
    assert render("C")["information_order"] == ["conclusion", "cause", "action"]


def test_information_order_medical_safety():
    assert render("E")["information_order"] == ["conclusion", "action", "cause"]
    assert render("E")["medical_attention"] is True


def test_evidence_level2_plain_language():
    items = evidence_level2(BUNDLE)
    assert "深睡占比比你最近自己的通常水平低一些" in items
    assert "静息心率比你最近自己的通常水平高一些" in items
    assert "静息心率的这种变化已经持续 2 天" in items
    for sentence in items:
        for jargon in ("MAD", "Theil-Sen", "Spearman", "robust z", "z-score"):
            assert jargon.lower() not in sentence.lower()


def test_evidence_level2_within_range_not_surfaced():
    items = evidence_level2(BUNDLE)
    assert not any("心率" == i for i in items)
    assert not any("WITHIN" in i for i in items)


def test_labels_are_five_fixed_states():
    assert render("A")["product_state_label"] == "整体稳定"
    assert render("B")["product_state_label"] == "有变化，但无需特别处理"
    assert render("C")["product_state_label"] == "今天值得调整"
    assert render("D")["product_state_label"] == "目前无法可靠判断"
    assert render("E")["product_state_label"] == "健康安全关注"


def test_updated_at_local_hhmm():
    # 2026-08-17T02:00:00Z == 10:00 Asia/Shanghai
    assert render()["updated_at_local_hhmm"] == "10:00"


def test_signature_stability_across_refresh():
    s1 = judgment_signature(DR, "C")
    s2 = judgment_signature(dict(DR), "C")
    assert s1 == s2
