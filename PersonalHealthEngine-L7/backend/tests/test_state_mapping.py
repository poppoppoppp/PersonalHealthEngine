"""Unit tests: five-state mapping (§11) and information order (§12)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from l7.rendering.renderer import map_product_state, judgment_signature, parse_actions


def dr(**kw):
    base = {
        "overall_state": "STABLE",
        "primary_hypothesis_type": "NO_SIGNIFICANT_FINDING",
        "secondary_hypothesis_type": None,
        "confidence": "MODERATE",
        "recommended_actions_json": "[]",
        "medical_review_state": "BYPASSED",
        "reasoning_summary": "稳定。",
    }
    base.update(kw)
    return base


def test_stable_maps_to_A():
    assert map_product_state(dr(), symptom_context_active=False) == "A"


def test_mild_change_maps_to_B():
    assert map_product_state(dr(overall_state="MILD_CHANGE"), False) == "B"


def test_notable_change_maps_to_C():
    assert map_product_state(dr(overall_state="NOTABLE_CHANGE",
                                primary_hypothesis_type="SLEEP_DEFICIT"), False) == "C"


def test_insufficient_evidence_maps_to_D():
    assert map_product_state(dr(overall_state="INSUFFICIENT_EVIDENCE",
                                primary_hypothesis_type="UNKNOWN"), False) == "D"


def test_unknown_very_low_maps_to_D():
    assert map_product_state(dr(overall_state="MILD_CHANGE",
                                primary_hypothesis_type="UNKNOWN",
                                confidence="VERY_LOW"), False) == "D"


def test_acute_illness_maps_to_E():
    assert map_product_state(dr(primary_hypothesis_type="ACUTE_ILLNESS_SUSPECTED"), False) == "E"


def test_medical_required_maps_to_E():
    assert map_product_state(dr(medical_review_state="REQUIRED"), False) == "E"


def test_medical_unavailable_maps_to_E():
    assert map_product_state(dr(medical_review_state="UNAVAILABLE"), False) == "E"


def test_symptom_context_maps_to_E():
    assert map_product_state(dr(), symptom_context_active=True) == "E"


def test_E_precedence_over_C_and_D():
    assert map_product_state(dr(overall_state="NOTABLE_CHANGE",
                                medical_review_state="PERFORMED"), False) == "E"
    assert map_product_state(dr(overall_state="INSUFFICIENT_EVIDENCE",
                                primary_hypothesis_type="ACUTE_ILLNESS_SUSPECTED"), False) == "E"


def test_actions_capped_at_three():
    d = dr(recommended_actions_json='["a","b","c","d","e"]')
    assert parse_actions(d, "C") == ["a", "b", "c"]


def test_stable_state_renders_zero_actions():
    d = dr(recommended_actions_json='["保持规律作息","今天按原计划生活"]')
    assert parse_actions(d, "A") == []


def test_invalid_actions_json_is_safe():
    d = dr(recommended_actions_json="not-json")
    assert parse_actions(d, "C") == []


def test_signature_ignores_irrelevant_row_metadata():
    a = dr()
    b = dr()
    b["created_at_utc"] = "2026-01-01T00:00:00+00:00"
    b["id"] = 999
    assert judgment_signature(a, "A") == judgment_signature(b, "A")


def test_signature_changes_with_actions_or_state():
    a = dr()
    b = dr(recommended_actions_json='["今天降低训练强度"]')
    assert judgment_signature(a, "C") != judgment_signature(b, "C")
    assert judgment_signature(a, "A") != judgment_signature(a, "B")
